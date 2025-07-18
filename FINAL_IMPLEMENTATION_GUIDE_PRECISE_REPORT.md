# 终极实现指南：精确报告 (Precise Report)

本文档提供了实现“精确报告”功能的最终版、端到端的代码实现方案。该方案整合了我们所有的讨论和优化，旨在生成来源精确、结构清晰、内容详实的高质量研究报告。

---

## 0. 核心流程概览

1.  **用户选择 "Precise Report" 模式**，后端 `ResearchConductor` 启动 `local_precise` 分支。
2.  **(不变) 加载文档**: `DocumentLoader` 加载所有本地文档。
3.  **阶段一：LLM批量筛选**: 一个**新的 `DocumentFilter`** 使用 `FAST_LLM` 对所有文档进行**单阶段批量筛选**，选出 Top-K 篇相关文档。
4.  **阶段二：LLM深度提取**: 一个**重构的 `document_summary` 模块**使用 `STRATEGIC_LLM` 对这 K 篇文档进行**逐篇的、复杂的结构化信息提取**。
5.  **阶段三：格式化整合**: 一个**重构的 `formatters` 模块**将提取出的结构化信息整合成最终的 Markdown 上下文。
6.  **(不变) 报告生成**: 将最终上下文送入 `ReportGenerator` 生成报告。

---

## 1. 新增/修改文件一：LLM驱动的文档筛选器

**目标**: 实现一个纯 LLM 驱动的、健壮的批量筛选器。

**新增文件**: `gpt_researcher/document/llm_document_filter.py`

**代码内容**:

```python
# gpt_researcher/document/llm_document_filter.py

import asyncio
import json_repair
import re
from typing import List, Dict

from ..config import Config
from ..utils.llm import create_chat_completion

class LLMDocumentFilter:
    def __init__(self, cfg: Config):
        if not cfg:
            raise ValueError("Config object cannot be None.")
        self.cfg = cfg

    def _get_word_preview(self, text: str, num_words: int = 5000) -> str:
        return " ".join(text.split()[:num_words])

    def _batch_documents(self, documents: List[Dict], batch_size: int = 10) -> List[List[Dict]]:
        return [documents[i:i + batch_size] for i in range(0, len(documents), batch_size)]

    def _create_filtering_prompt(self, batch_previews: List[Dict], query: str) -> str:
        preview_texts = "\n".join([f"  - **{p['filename']}**: {p['preview']}" for p in batch_previews])
        prompt = (
            "You are a highly efficient document classification assistant. Your task is to identify documents "
            f"that are directly relevant to the following research query:\n\n**Research Query:** \"{query}\"\n\n"
            "Review the following list of document previews. A preview consists of a filename and the first 5000 words of its content.\n\n"
            "**Documents to Review:**\n"
            f"{preview_texts}\n\n"
            "**Instructions:**\n"
            "Identify all documents from the list that are **highly and directly relevant** to the research query. "
            "Your output **MUST** be a single, valid JSON list containing only the filenames of the relevant documents. "
            "If no documents are relevant, you **MUST** return an empty list `[]`.\n\n"
            "**Correct Output Example:**\n`[\"report-A.pdf\", \"article-B.pdf\"]`\n\n"
            "**Incorrect Output Example:**\n`Here is the list: [\"report-A.pdf\"]`\n\n"
            "**Your JSON Output:**"
        )
        return prompt

    def _extract_json_from_response(self, response_text: str) -> list:
        match = re.search(r'\[(.*?)\]', response_text)
        if match:
            try:
                return json_repair.loads(match.group(0))
            except Exception:
                return []
        return []

    async def filter_documents(self, documents: List[Dict], query: str) -> List[str]:
        batches = self._batch_documents(documents)
        tasks = []
        for batch in batches:
            previews = [{"filename": doc.get("metadata", {}).get("source", ""), "preview": self._get_word_preview(doc.get("raw_content", ""))} for doc in batch]
            prompt = self._create_filtering_prompt(previews, query)
            task = create_chat_completion(
                model=self.cfg.fast_llm_model,
                messages=[{"role": "user", "content": prompt}],
                llm_provider=self.cfg.fast_llm_provider,
                max_tokens=512,
                llm_kwargs=self.cfg.llm_kwargs,
            )
            tasks.append(task)
        responses = await asyncio.gather(*tasks)
        relevant_filenames = []
        for response in responses:
            relevant_filenames.extend(self._extract_json_from_response(response))
        return list(set(relevant_filenames))
```

---

## 2. 新增/修改文件二：LLM驱动的深度提取器

**目标**: 实现一个能提取详细来源、进行智能主题归类、并做深度总结的模块。

**新增文件**: `gpt_researcher/actions/llm_extractor.py`

**代码内容**:

```python
# gpt_researcher/actions/llm_extractor.py

import json_repair
from typing import Dict
from ..config import Config
from ..utils.llm import create_chat_completion, extract_final_content

def _create_extraction_prompt(document_content: str, user_query: str) -> str:
    prompt = (
        "You are an expert research analyst and data extractor. Your task is to meticulously analyze the provided document "
        f"in relation to the user's research query: \"{user_query}\". You must follow all instructions precisely "
        "and format your output as a single, valid JSON object.\n\n"
        "**Full Document Content:**\n---\n"
        f"{document_content}"
        "\n---\n\n"
        "**Your Tasks (Instructions):**\n\n"
        "**1. Extract Source Details:**\n"
        "   - Analyze the document to identify its type ('arXiv', 'WeChat', or 'Other').\n"
        "   - **For arXiv papers:** Extract 'title', 'authors' (as a list of strings), 'url' (the arXiv link), and 'publication_date'.\n"
        "   - **For WeChat articles:** Extract 'title', 'publisher' (the Official Account Name), and 'publication_date'.\n"
        "   - If a field is not present, you MUST use `null` as its value.\n\n"
        "**2. Assign a Topic:**\n"
        "   - You must assign a single, concise topic based on these rules in order:\n"
        "     a. If the user query is a clear topic AND the document is highly relevant, use the query's topic.\n"
        "     b. If the query is broad, create a concise topic summarizing the query's intent. If the document fits, use this topic.\n"
        "     c. Otherwise, generate a new concise topic that best describes the document's own content.\n\n"
        "**3. Create a Core Summary:**\n"
        "   - **Main Points:** Summarize the document's core arguments and findings in a list of clear points.\n"
        "   - **Key Data:** Extract all specific, quantifiable data (numbers, percentages, metrics). MUST be precise. Return `[]` if none.\n\n"
        "**Final Output Requirement:**\n"
        "Your final output MUST be a single JSON object. Do not include any text or markdown before or after the JSON. "
        "The structure MUST be exactly as follows:\n"
        '```json\n'
        '{\n'
        '  "source_details": { "type": "...", "title": "...", "authors": ["..."], "publisher": "...", "url": "...", "publication_date": "..." },\n'
        '  "assigned_topic": "...",\n'
        '  "core_summary": { "main_points": ["..."], "key_data": ["..."] }\n'
        '}\n'
        '```'
    )
    return prompt

async def extract_and_summarize_document(document: Dict, query: str, cfg: Config) -> Dict:
    document_content = document.get("raw_content", "")
    prompt = _create_extraction_prompt(document_content, query)
    try:
        raw_response = await create_chat_completion(
            model=cfg.strategic_llm_model,
            messages=[{"role": "user", "content": prompt}],
            llm_provider=cfg.strategic_llm_provider,
            max_tokens=2048,
            llm_kwargs=cfg.llm_kwargs,
        )
        final_content = extract_final_content(raw_response)
        return json_repair.loads(final_content)
    except Exception as e:
        filename = document.get("metadata", {}).get("source", "Unknown")
        print(f"Error processing document {filename} with STRATEGIC_LLM: {e}")
        return {"source_details": {"title": filename, "type": "Error"}, "assigned_topic": "Processing Error", "core_summary": {"main_points": [f"Failed to process document due to error: {e}"], "key_data": []}}
```

---

## 3. 新增/修改文件三：最终上下文格式化器

**目标**: 将第二阶段产出的复杂 JSON 优雅地格式化为 Markdown。

**新增文件**: `gpt_researcher/utils/formatters.py`

**代码内容**:

```python
# gpt_researcher/utils/formatters.py

from typing import List, Dict

def format_final_context(structured_data: List[Dict]) -> str:
    if not structured_data:
        return "No relevant information could be extracted from the provided documents."
    final_context_parts = []
    for item in structured_data:
        source_details = item.get("source_details", {})
        assigned_topic = item.get("assigned_topic", "N/A")
        core_summary = item.get("core_summary", {})
        source_title = source_details.get("title", "Unknown Title")
        context_part = f"## {source_title}\n\n"
        context_part += "| Attribute | Details |\n|---|---|\n"
        context_part += f"| **Assigned Topic** | {assigned_topic} |\n"
        for key, value in source_details.items():
            if key != 'title' and value:
                display_key = key.replace('_', ' ').title()
                if isinstance(value, list):
                    context_part += f"| **{display_key}** | {', '.join(value)} |\n"
                else:
                    context_part += f"| **{display_key}** | {value} |\n"
        context_part += "\n"
        main_points = core_summary.get("main_points", [])
        if main_points:
            context_part += "### Core Findings\n"
            for point in main_points:
                context_part += f"- {point}\n"
            context_part += "\n"
        key_data = core_summary.get("key_data", [])
        if key_data:
            context_part += "### Key Data & Metrics\n"
            for data_point in key_data:
                context_part += f"- **{data_point}**\n"
            context_part += "\n"
        final_context_parts.append(context_part)
    return "\n---\n\n<br>\n\n---\n\n".join(final_context_parts)
```

---

## 4. 新增/修改文件四：通用工具函数

**目标**: 提供一个处理 `<think>` 标签的工具函数。

**新增/修改文件**: `gpt_researcher/utils/llm.py`

**代码内容**:

```python
# In gpt_researcher/utils/llm.py, add this function

def extract_final_content(response_text: str) -> str:
    """
    Extracts the content after the </think> tag if it exists, otherwise returns the original text.
    """
    if '</think>' in response_text:
        parts = response_text.split('</think>', 1)
        if len(parts) > 1:
            return parts[1].strip()
    return response_text.strip()
```

---

## 5. 后端主流程修改：集成最终工作流

**目标**: 将所有新模块集成到 `ResearchConductor` 的 `local_precise` 分支中。

**修改文件**: `gpt_researcher/skills/researcher.py`

**操作**:

1.  **添加新导入**:
    ```python
    from ..document.llm_document_filter import LLMDocumentFilter
    from ..actions.llm_extractor import extract_and_summarize_document
    from ..utils.formatters import format_final_context
    from ..utils.llm import extract_final_content
    ```
2.  **用新逻辑覆盖 `conduct_research` 方法中的 `local_precise` 分支**:
    ```python
    # In ResearchConductor.conduct_research method
    elif self.researcher.report_source == "local_precise":
        self.logger.info("Using Precise Report with LLM-native filtering workflow.")
        all_documents = await DocumentLoader(self.researcher.cfg.doc_path).load()
        self.logger.info(f"Loaded {len(all_documents)} documents for filtering.")
        # Stage 1: LLM-based Filtering
        llm_filter = LLMDocumentFilter(cfg=self.researcher.cfg)
        await stream_output("logs", f"Filtering {len(all_documents)} documents with FAST_LLM...", self.researcher.websocket)
        relevant_filenames = await llm_filter.filter_documents(all_documents, self.researcher.query)
        top_documents = [doc for doc in all_documents if doc.get("metadata", {}).get("source") in relevant_filenames]
        self.logger.info(f"Filtered down to {len(top_documents)} most relevant documents.")
        # Stage 2: In-depth Extraction & Summarization
        await stream_output("logs", f"Extracting information from {len(top_documents)} documents with STRATEGIC_LLM...", self.researcher.websocket)
        summary_tasks = [
            extract_and_summarize_document(doc, self.researcher.query, self.researcher.cfg)
            for doc in top_documents
        ]
        structured_data = await asyncio.gather(*summary_tasks)
        self.logger.info(f"Generated {len(structured_data)} structured summaries.")
        # Stage 3: Context Aggregation
        await stream_output("logs", "Aggregating final context...", self.researcher.websocket)
        research_data = format_final_context(structured_data)
        self.logger.info(f"Final context aggregated. Length: {len(research_data)} chars.")
    ```
