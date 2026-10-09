"""StructFlow 多模型异步抽取器。

StructFlowExtractor 封装单个被测大模型的零样本抽取调用：
- 输出 JSON 截取与解析、Pydantic 装甲校验、脱水压缩；
- 独立信号量限流 + 全局去重防复读；
- 网络错误指数退避重试，结构错误/幻觉直接拦截并分类留痕。
"""
import re
import json
import time
import asyncio
import difflib
from typing import Tuple, Dict, Optional
from pydantic import ValidationError
from openai import OpenAI, AsyncOpenAI, APITimeoutError, APIConnectionError, RateLimitError

from .schemas import MedicalRecordSchema


class StructFlowExtractor:
    def __init__(self, name: str, api_key: str, base_url: str, concurrency: int):
        self.name = name
        _name = name.lower()
        self.is_reasoning = ("r1" in _name or "reasoning" in _name or "kimi" in _name)
        self.timeout = 180.0 if self.is_reasoning else 60.0
        self.max_tokens = 8000 if self.is_reasoning else 2500
        if "kimi" in _name:
            self.temperature = 1.0
        elif self.is_reasoning:
            self.temperature = 0.6
        else:
            self.temperature = 0.1
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=self.timeout)

        self.sem = asyncio.Semaphore(concurrency)
        self.history: list[str] = []
        self.dedup_lock = asyncio.Lock()

    @staticmethod
    def clean_json_string(text: str) -> str:
        if not text:
            return "{}"
        text = re.sub(r'<think>.*?(?:</think>|$)', '', text, flags=re.DOTALL)
        s = text.find('{')
        e = text.rfind('}')
        if s != -1 and e != -1 and e >= s:
            return text[s:e+1]
        return text.strip()

    async def _evaluate(self, system_prompt: str, user_text: str):
        raw_response = ""
        for attempt in range(4):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text}
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens
                )
                raw_response = resp.choices[0].message.content or ""
                cleaned = self.clean_json_string(raw_response)
                parsed = json.loads(cleaned)

                validated = MedicalRecordSchema(**parsed)
                concise = validated.model_dump(
                    exclude_none=True, exclude_defaults=True, exclude_unset=True)
                if not concise:
                    raise ValueError("JSON 结构合规但无有效业务内容 (空提取)")
                cj = json.dumps(concise, ensure_ascii=False)

                # 全局去重（本模型已完成的全部输出，加锁原子化）
                dup_sim = 0.0
                async with self.dedup_lock:
                    for prev in self.history:
                        s = difflib.SequenceMatcher(None, cj, prev).ratio()
                        if s > dup_sim:
                            dup_sim = s
                    if dup_sim > 0.90:
                        duplicate = True
                    else:
                        duplicate = False
                        self.history.append(cj)
                if duplicate:
                    if attempt < 3:
                        await asyncio.sleep(2 + attempt)
                        continue
                    return raw_response, "", None, f"雷同阻断: 与历史输出相似度 {dup_sim:.2%}"

                return raw_response, cj, concise, None

            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return raw_response, "", None, f"网关错误({type(e).__name__}): {str(e)[:120]}"
            except json.JSONDecodeError as e:
                if attempt < 3:
                    await asyncio.sleep(1 + attempt)
                    continue
                return raw_response, "", None, f"JSON结构撕裂/截断: {str(e)[:100]}"
            except ValidationError as e:
                return raw_response, "", None, f"Schema幻觉拦截: {str(e)[:200]}"
            except ValueError as e:
                return raw_response, "", None, f"业务校验失败: {str(e)[:120]}"
            except Exception as e:
                if "429" in str(e) and attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return raw_response, "", None, f"底层异常: {str(e)[:150]}"
        return raw_response, "", None, "重试4次仍失败"

    async def run(self, system_prompt: str, user_text: str):
        async with self.sem:
            return await self._evaluate(system_prompt, user_text)


class StructFlowJudge:
    """Phase 2 同步裁判模型（LLM-as-a-Judge）。

    对专家初步抽取结果做仲裁融合，输出经 Pydantic 装甲脱水的定稿 JSON；
    网络错误退避重试，其余校验失败直接分类返回，交由人工复核兜底。
    """

    def __init__(self, api_key: str, base_url: str, model_name: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
        self.model_name = model_name

    def evaluate_judge(self, system_prompt: str, user_text: str):
        raw_response = ""
        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text}
                    ],
                    temperature=0.1,
                    max_tokens=3000
                )
                raw_response = response.choices[0].message.content or ""

                start_idx, end_idx = raw_response.find('{'), raw_response.rfind('}')
                if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
                    cleaned_text = raw_response[start_idx:end_idx + 1]
                else:
                    cleaned_text = raw_response.strip()

                parsed_json = json.loads(cleaned_text)

                validated_model = MedicalRecordSchema(**parsed_json)
                concise_dict = validated_model.model_dump(
                    exclude_none=True, exclude_defaults=True, exclude_unset=True)

                if not concise_dict:
                    raise ValueError("无有效业务内容")

                return raw_response, json.dumps(concise_dict, ensure_ascii=False), concise_dict, None

            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                if attempt < max_attempts - 1:
                    time.sleep(2 ** attempt)
                    continue
                return raw_response, "", None, f"网关崩溃: {str(e)}"
            except Exception as e:
                return raw_response, "", None, f"校验失败: {str(e)}"
