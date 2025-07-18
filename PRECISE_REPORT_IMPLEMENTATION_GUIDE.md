# “精确报告 (Precise Report)” 功能实现指南

本文档提供了实现“精确报告”功能的详细、端到端的代码修改和新增说明。该功能旨在通过改进的上下文处理流程，生成来源更准确、逻辑更严谨的研究报告。

---

## 概览

我们将通过以下步骤实现新功能：

1.  **前端修改**：在UI上添加一个新的报告源选项。
2.  **后端主流程修改**：在 `ResearchConductor` 中为新选项创建一个专门的处理分支。
3.  **新增模块**：创建三个新的 Python 模块来实现“文档筛选”、“单篇摘要”和“上下文整合”的核心逻辑。

---

## 1. 前端修改：添加 "Precise Report" 选项

**目标**：让用户可以在前端选择新的报告模式。

**文件**: `frontend/index.html` (或等效的 Next.js/React 组件)

**操作**: 在报告来源的下拉菜单中，添加一个新的 `<option>`。

```html
<!-- In frontend/index.html -->
<label for="reportSource">Report Source</label>
<select class="form-control" id="reportSource">
    <option value="web">Web</option>
    <option value="local">My Documents</option>
    <!-- 👇 添加以下新行 👇 -->
    <option value="local_precise">Precise Report (My Documents)</option>
    <!-- 👆 添加以上新行 👆 -->
</select>
```

**说明**:
*   `value="local_precise"` 是关键。这个值将被发送到后端，作为区分新旧流程的标识符。
*   显示文本 "Precise Report (My Documents)" 能清晰地告知用户这个选项的功能。
*   现有前端 JavaScript 无需修改。

---

## 2. 新增模块一：文档级智能筛选

**目标**：创建一个新模块，负责从所有本地文档中筛选出与查询最相关的 Top-N 篇。

**新增文件**: `gpt_researcher/document/document_filter.py`

**代码内容**:

```python
# gpt_researcher/document/document_filter.py

import asyncio
from typing import List, Dict
from langchain.embeddings.base import Embeddings
import numpy as np

class DocumentFilter:
    def __init__(self, embeddings: Embeddings):
        if not embeddings:
            raise ValueError("Embeddings model cannot be None.")
        self.embeddings = embeddings

    def _extract_metadata_text(self, document: Dict[str, any]) -> str:
        filename = document.get("url", "")
        content_preview = document.get("raw_content", "")[:500]
        return f"Title: {filename}\n\nPreview: {content_preview}"

    async def filter_top_n_documents(self, documents: List[Dict[str, any]], query: str, top_n: int = 10) -> List[Dict[str, any]]:
        if not documents:
            return []
        metadata_texts = [self._extract_metadata_text(doc) for doc in documents]
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

## 3. 新增模块二：单篇文档摘要与提取

**目标**：创建一个新模块，负责对单篇文档进行深度阅读，并提取结构化的摘要信息。

**新增文件**: `gpt_researcher/actions/document_summary.py`

**代码内容**:

```python
# gpt_researcher/actions/document_summary.py

import json_repair
from typing import Dict
from ..config import Config
from ..utils.llm import create_chat_completion

def _create_summarization_prompt(document_content: str, document_filename: str, query: str) -> str:
    # Using simple string concatenation to avoid f-string parsing issues.
    prompt = (
        "You are a professional research analyst. Your task is to carefully read the following document "
        "and extract key information relevant to the main research topic.\n\n"
        f"**Main Research Topic:** \"{query}\"\n\n"
        "**Document Content:**\n---\n"
        f"{document_content}\n---\n\n"
        "**Instructions:**\n"
        "Based on the document content provided above, please perform the following tasks:\n"
        "1.  **Summarize Core Findings:** Write a concise summary of the document's core findings "
        "as they relate to the main research topic.\n"
        "2.  **Extract Key Data:** Identify and list any specific, quantifiable data points or key metrics. "
        "If none are found, return an empty list.\n"
        f"3.  **Source Attribution:** The source for all this information is the document titled \"{document_filename}\".\n\n"
        "**Output Format:**\n"
        "You MUST return your response as a single, valid JSON object. Do not add any explanatory text "
        "before or after the JSON. The JSON object must adhere to the following structure:\n\n"
        "{\n"
        f'  \"source_document\": \"{document_filename}\",\n'
        '  \"summary\": \"A concise summary of the document's core findings related to the research topic.\",\n'
        '  \"key_data\": [\"A specific data point or finding.\", \"Another key metric or statistic.\"]\n'
        "}"
    )
    return prompt

async def summarize_single_document(document: Dict[str, any], query: str, cfg: Config) -> Dict:
    document_content = document.get("raw_content", "")
    document_filename = document.get("url", "Unknown Source")
    if not document_content:
        return {"source_document": document_filename, "summary": "Error: Document content is empty.", "key_data": []}
    prompt = _create_summarization_prompt(document_content, document_filename, query)
    try:
        response = await create_chat_completion(
            model=cfg.smart_llm_model,
            messages=[{"role": "user", "content": prompt}],
            llm_provider=cfg.smart_llm_provider,
            max_tokens=1000,
            llm_kwargs=cfg.llm_kwargs,
        )
        return json_repair.loads(response)
    except Exception as e:
        print(f"Error summarizing document {document_filename}: {e}")
        return {"source_document": document_filename, "summary": f"Error during summarization: {e}", "key_data": []}
```

---

## 4. 新增模块三：上下文格式化工具

**目标**：创建一个辅助函数，将第二阶段生成的结构化 JSON 列表，转换成一个对最终报告生成器友好的、可读的 Markdown 字符串。

**新增/修改文件**: `gpt_researcher/utils/formatters.py`

**代码内容**:

```python
# gpt_researcher/utils/formatters.py

from typing import List, Dict

def format_structured_context(structured_summaries: List[Dict]) -> str:
    if not structured_summaries:
        return "No relevant information found in the provided documents."
    final_context_parts = []
    for summary_obj in structured_summaries:
        source = summary_obj.get("source_document", "Unknown Source")
        summary = summary_obj.get("summary", "No summary provided.")
        key_data = summary_obj.get("key_data", [])
        context_part = f"### Source: {source}\n\n"
        context_part += f"**Summary of Findings:**\n{summary}\n\n"
        if key_data:
            context_part += "**Key Data Points:**\n"
            for data_point in key_data:
                context_part += f"- {data_point}\n"
        final_context_parts.append(context_part)
    return "\n---\n\n".join(final_context_parts)
```

---

## 5. 后端主流程修改：集成新流程

**目标**：将以上三个新模块集成到主研究流程中。

**修改文件**: `gpt_researcher/skills/researcher.py`

**操作**: 在 `ResearchConductor.conduct_research` 方法中添加新的 `elif` 分支。

```python
# gpt_researcher/skills/researcher.py

import asyncio
from ..document import DocumentLoader
from ..document.document_filter import DocumentFilter
from ..actions.document_summary import summarize_single_document
from ..utils.formatters import format_structured_context

class ResearchConductor:
    async def conduct_research(self):
        # ... (existing logic) ...
        elif self.researcher.report_source == "local_precise":
            self.logger.info("Using Precise Report (Local Documents) workflow.")
            all_documents = await DocumentLoader(self.researcher.cfg.doc_path).load()
            self.logger.info(f"Loaded {len(all_documents)} documents for precise filtering.")
            document_filter = DocumentFilter(embeddings=self.researcher.memory.get_embeddings())
            await stream_output("logs", "Filtering relevant documents...", self.researcher.websocket)
            top_n_documents = await document_filter.filter_top_n_documents(
                documents=all_documents, query=self.researcher.query, top_n=10
            )
            self.logger.info(f"Filtered down to {len(top_n_documents)} most relevant documents.")
            await stream_output("logs", f"Summarizing {len(top_n_documents)} documents...", self.researcher.websocket)
            summary_tasks = [
                summarize_single_document(doc, self.researcher.query, self.researcher.cfg)
                for doc in top_n_documents
            ]
            structured_summaries = await asyncio.gather(*summary_tasks)
            self.logger.info(f"Generated {len(structured_summaries)} structured summaries.")
            await stream_output("logs", "Aggregating final context...", self.researcher.websocket)
            research_data = format_structured_context(structured_summaries)
            self.logger.info(f"Final context aggregated. Length: {len(research_data)} chars.")
        # ... (rest of the method) ...
        self.researcher.context = research_data
        return self.researcher.context
```
