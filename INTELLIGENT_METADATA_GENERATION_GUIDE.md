# 智能元信息生成实现指南

本报告为您提出的 `DocumentFilter` 问题提供了详细的、可执行的代码解决方案。该方案将原有的简单元信息提取方式，升级为由 `FAST_LLM` 驱动的智能元信息生成流程，以显著提升文档筛选的准确性。

---

## 1. 核心问题

*   **元信息质量低**：原方案使用“文件名 + 前500字符”作为文档的代表性文本，过于简陋，无法准确反映文档核心内容。
*   **实现不健壮**：原方案对 `document` 对象的结构处理不够严谨。

---

## 2. 解决方案：LLM 驱动的元信息生成

我们将修改 `DocumentFilter` 类，使其能够调用 `FAST_LLM` 为每篇文档生成一段高质量的、包含核心思想的摘要，并以此作为后续向量搜索的依据。

---

## 3. 详细代码修改

**目标文件**: `gpt_researcher/document/document_filter.py`

**操作**: 请用以下完整代码**覆盖** `gpt_researcher/document/document_filter.py` 文件的全部内容。

```python
# gpt_researcher/document/document_filter.py

import asyncio
from typing import List, Dict
from langchain.embeddings.base import Embeddings
import numpy as np

# 导入 Config 和 LLM 调用工具
from ..config import Config
from ..utils.llm import create_chat_completion

class DocumentFilter:
    def __init__(self, embeddings: Embeddings, cfg: Config):
        if not embeddings:
            raise ValueError("Embeddings model cannot be None.")
        if not cfg:
            raise ValueError("Config object cannot be None.")
        self.embeddings = embeddings
        self.cfg = cfg

    def _create_metadata_generation_prompt(self, document_content: str) -> str:
        truncated_content = document_content[:15000].replace("\"", "'")
        prompt = "Read the following document carefully. Your task is to generate a concise and informative metadata summary that captures the core essence of the document. This summary will be used for a semantic search to find relevant documents.\n\n**Document Content:**\n---\n" + truncated_content + "\n---\n\n**Instructions:**\nBased on the document, provide a short summary (2-3 sentences) that includes:\n- The main topic, argument, or finding of the document.\n- Key technologies, entities, or conclusions mentioned.\nFocus on creating a dense summary of the most important information. Do not add any preamble like \'Here is the summary\'.\n\n**Metadata Summary:**"
        return prompt

    async def _generate_metadata_with_llm(self, document: Dict[str, any]) -> str:
        content = document.get("raw_content", "")
        filename = document.get("metadata", {}).get("source", "Unknown Source")
        if not content:
            return f"Title: {filename}"
        prompt = self._create_metadata_generation_prompt(content)
        try:
            summary = await create_chat_completion(
                model=self.cfg.fast_llm_model,
                messages=[{"role": "user", "content": prompt}],
                llm_provider=self.cfg.fast_llm_provider,
                max_tokens=256,
                llm_kwargs=self.cfg.llm_kwargs,
            )
            return f"Source Document: {filename}\n\nCore Summary: {summary.strip()}"
        except Exception as e:
            print(f"LLM metadata generation failed for {filename}: {e}. Falling back to basic metadata extraction.")
            return f"Source Document: {filename}\n\nContent Preview: {content[:500]}"

    async def filter_top_n_documents(self, documents: List[Dict[str, any]], query: str, top_n: int = 10) -> List[Dict[str, any]]:
        if not documents:
            return []
        metadata_generation_tasks = [self._generate_metadata_with_llm(doc) for doc in documents]
        metadata_texts = await asyncio.gather(*metadata_generation_tasks)
        query_vector, document_vectors = await asyncio.gather(
            self.embeddings.aembed_query(query),
            self.embeddings.aembed_documents(metadata_texts)
        )
        query_vec_norm = np.linalg.norm(query_vector)
        doc_vecs_norm = np.linalg.norm(document_vectors, axis=1)
        if query_vec_norm == 0 or np.any(doc_vecs_norm == 0):
            return []
        similarities = np.dot(document_vectors, query_vector) / (doc_vecs_norm * query_vec_norm)
        top_n_indices = np.argsort(similarities)[::-1][:top_n]
        return [documents[i] for i in top_n_indices]
```

---

## 4. 后端主流程修改

**目标文件**: `gpt_researcher/skills/researcher.py`

**操作**: 在 `ResearchConductor.conduct_research` 方法中，当创建 `DocumentFilter` 实例时，需要将 `Config` 对象传递给它。

```python
# gpt_researcher/skills/researcher.py

from ..document.document_filter import DocumentFilter
# ... (other imports)

class ResearchConductor:
    async def conduct_research(self):
        # ... (existing code)
        elif self.researcher.report_source == "local_precise":
            self.logger.info("Using Precise Report (Local Documents) workflow.")
            all_documents = await DocumentLoader(self.researcher.cfg.doc_path).load()
            self.logger.info(f"Loaded {len(all_documents)} documents for precise filtering.")
            document_filter = DocumentFilter(embeddings=self.researcher.memory.get_embeddings(), cfg=self.researcher.cfg)
            await stream_output("logs", "Generating metadata summaries for all documents...", self.researcher.websocket)
            top_n_documents = await document_filter.filter_top_n_documents(
                documents=all_documents, query=self.researcher.query, top_n=10
            )
            self.logger.info(f"Filtered down to {len(top_n_documents)} most relevant documents.")
            # ... (rest of the new branch remains the same)
```
