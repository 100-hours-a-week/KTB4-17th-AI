"""KURE-v1 / arctic-embed-v2-ko ONNX int8 CPU 임베더 및 한국어 구어체 전처리."""

from __future__ import annotations

import re
import threading
from functools import lru_cache
from typing import Any, Protocol

import numpy as np
import onnxruntime as ort
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings

# 3회 이상 연속 반복되는 동일 문자를 2회로 축약 ("ㅋㅋㅋㅋㅋㅋ" -> "ㅋㅋ")
_REPEAT_CHARS = re.compile(r"(.)\1{2,}")

# 단독으로 쓰였을 때 정보 가치가 없는 단답/호응 사전
_TRIVIAL_REACTIONS = {
    "ㅇㅇ",
    "ㅇㅋ",
    "ㅋㅋ",
    "ㅎㅎ",
    "ㅠㅠ",
    "ㅜㅜ",
    "네",
    "넵",
    "넹",
    "응",
    "어",
    "맞아",
    "맞아요",
    "그렇구나",
    "오",
    "아하",
    "좋아",
    "좋아요",
    "그래",
    "그래요",
    "고마워",
    "고마워요",
}
_ONLY_JAMO_OR_PUNCT = re.compile(r"^[ㅋㅎㅠㅜㅇ!?.,~\s]+$")
_NUMBERS_RE = re.compile(r"\d+")


def normalize_chat_text(text: str) -> str:
    """반복 문자를 2글자로 축약하고 다중 공백만 정리한다. 'ㅋㅋ' 등 농담 신호는 보존한다."""
    collapsed = _REPEAT_CHARS.sub(r"\1\1", text.strip())
    return re.sub(r"\s+", " ", collapsed)


def extract_numbers(text: str) -> list[str]:
    """문장 내 숫자 토큰을 추출한다 ('3살' vs '5살' 처럼 숫자만 바뀐 문장의 NOOP 오판 방지용)."""
    return _NUMBERS_RE.findall(text)


def is_trivial_reaction(text: str) -> bool:
    """단일 사용자 발화가 정보가 없는 짧은 맞장구/감탄사인지 판별한다."""
    norm = normalize_chat_text(text)
    if len(norm) <= 1:
        return True
    return norm in _TRIVIAL_REACTIONS or bool(_ONLY_JAMO_OR_PUNCT.match(norm))


def is_batch_all_trivial(user_messages: list[str]) -> bool:
    """배치 내 사용자 메시지가 전부 짧은 반응뿐이면 True (LLM 추출 생략 대상)."""
    return bool(user_messages) and all(is_trivial_reaction(m) for m in user_messages)


class EmbedderProtocol(Protocol):
    async def encode_query(self, text: str) -> list[float]: ...
    async def encode(self, text: str) -> list[float]: ...
    async def encode_batch(self, texts: list[str]) -> list[list[float]]: ...


class OnnxTextEmbedder:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._lock = threading.Lock()
        self._session: ort.InferenceSession | None = None
        self._tokenizer: Any | None = None

    def _ensure_loaded(self) -> tuple[ort.InferenceSession, Any]:
        if self._session is not None and self._tokenizer is not None:
            return self._session, self._tokenizer
        with self._lock:
            if self._session is None:
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2
                opts.inter_op_num_threads = 1
                self._session = ort.InferenceSession(
                    str(self._settings.embedding_onnx_path),
                    sess_options=opts,
                    providers=["CPUExecutionProvider"],
                )
            if self._tokenizer is None:
                from tokenizers import Tokenizer

                tok = Tokenizer.from_file(str(self._settings.embedding_tokenizer_path))
                tok.enable_truncation(max_length=self._settings.embedding_max_length)
                tok.enable_padding(pad_id=1, pad_token="<pad>")
                self._tokenizer = tok
        return self._session, self._tokenizer

    def encode_batch_sync(self, texts: list[str], *, prefix: str = "") -> list[list[float]]:
        if not texts:
            return []
        session, tokenizer = self._ensure_loaded()
        normalized = [f"{prefix}{normalize_chat_text(t) or ' '}" for t in texts]
        encodings = tokenizer.encode_batch(normalized)

        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
        input_names = {inp.name for inp in session.get_inputs()}
        if "token_type_ids" in input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)

        outputs = session.run(None, feeds)
        token_embeddings = outputs[0]  # (batch, seq_len, 1024)

        if self._settings.embedding_pooling_mode.lower() == "cls":
            # KURE-v1 (bge-m3 계열) 및 arctic-embed-l-v2.0-ko 기본 CLS 풀링
            pooled = token_embeddings[:, 0, :].astype(np.float32)
        else:
            mask_expanded = np.expand_dims(attention_mask, axis=-1).astype(np.float32)
            summed = np.sum(token_embeddings * mask_expanded, axis=1)
            counts = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
            pooled = summed / counts

        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        normed = pooled / np.clip(norms, a_min=1e-12, a_max=None)
        return normed.astype(np.float32).tolist()

    async def encode_query(self, text: str) -> list[float]:
        prefix = self._settings.embedding_query_prefix
        res = await run_in_threadpool(self.encode_batch_sync, [text], prefix=prefix)
        return res[0]

    async def encode(self, text: str) -> list[float]:
        res = await run_in_threadpool(self.encode_batch_sync, [text])
        return res[0]

    async def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return await run_in_threadpool(self.encode_batch_sync, texts)


@lru_cache
def get_embedder() -> OnnxTextEmbedder:
    return OnnxTextEmbedder()
