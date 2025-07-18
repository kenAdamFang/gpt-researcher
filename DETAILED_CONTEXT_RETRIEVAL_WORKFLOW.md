# 核心研究流程：从计划到上下文的深度解析

本文档深入剖析 GPT-Researcher 在处理本地文档时，从“生成研究计划”到“整合最终上下文”的核心技术流程。我们将严格依据代码，揭示其内部工作机制。

**涉及的核心模块**:
- `gpt_researcher/skills/researcher.py` (`ResearchConductor`)
- `gpt_researcher/actions/query_processing.py` (`plan_research_outline`)
- `gpt_researcher/prompts.py` (`generate_search_queries_prompt`)
- `gpt_researcher/skills/context_manager.py` (`ContextManager`)
- `gpt_researcher/context/compression.py` (`ContextCompressor`)

---

## 流程概览

整个流程可以被看作一个“**分而治之**”的策略：

1.  **分解 (Divide)**: 将一个宽泛的、高层次的研究任务，通过 LLM 分解成一系列具体的、可执行的子查询。
2.  **处理 (Conquer)**: 对每一个子查询，在已加载的本地文档中，通过向量搜索找到最相关的信息片段。
3.  **合并 (Combine)**: 将所有子查询找到的信息片段合并起来，形成一个丰富、全面的上下文，供最终报告生成使用。

---

## 第一步：研究计划与子查询生成

这个阶段的入口是 `ResearchConductor._get_context_by_web_search` 方法。尽管它的名字带有 "web_search"，但在本地文档模式下，它的核心职责是协调研究计划的生成。

```python
# in gpt_researcher/skills/researcher.py
async def _get_context_by_web_search(self, query, scraped_data: list | None = None, query_domains: list | None = None):
    # ... (MCP logic is skipped) ...

    # ► Key Step 1: Generate Sub-Queries
    sub_queries = await self.plan_research(query, query_domains)
    # ...
```

### 1.1 `plan_research` 方法

此方法调用 `plan_research_outline`，是生成研究计划的核心。

```python
# in gpt_researcher/skills/researcher.py
async def plan_research(self, query, query_domains=None):
    # ... (Initial web search for context, can be ignored for local files) ...

    # ► Key Step 2: Call to plan_research_outline
    outline = await plan_research_outline(
        query=query,
        # ... other params
    )
    return outline
```

### 1.2 `plan_research_outline` 与 Prompt

`plan_research_outline` (位于 `gpt_researcher/actions/query_processing.py`) 的主要作用是构建一个特定的 Prompt，并发送给 LLM，让 LLM 来为我们“出谋划策”。

这个 Prompt 的核心模板来自于 `gpt_researcher/prompts.py` 中的 `generate_search_queries_prompt`。

```python
# in gpt_researcher/prompts.py
def generate_search_queries_prompt(
    question: str,
    # ... other params
):
    # ...
    return f"""Write {max_iterations} google search queries to search online that form an objective opinion from the following task: "{task}"

You must respond with a list of strings in the following format: ["query 1", "query 2", "query 3"].
The response should contain ONLY the list.
"""
```

**工作机制解析**:

1.  **构建 Prompt**: 将用户的原始研究任务（`question`）嵌入到一个精心设计的指令模板中。这个指令要求 LLM 扮演一个“经验丰富的研究助理”，为给定的任务生成 N 个（`max_iterations`）Google 搜索查询。
2.  **LLM 的角色**: LLM 在这里不是直接回答问题，而是**作为规划者**。它利用其对世界的理解，将一个大的主题（例如“人工智能在医疗领域的应用”）分解成多个更小、更具体的探究角度（例如“AI 在医学影像分析中的最新进展”，“AI 用于药物发现的案例研究”，“可穿戴设备结合 AI 进行健康监测”等）。
3.  **输出格式**: Prompt 强制要求 LLM 以一个 JSON 格式的字符串列表（`["query 1", "query 2", ...]`）来返回结果，这便于程序直接解析，而无需进行复杂的文本处理。

**最终，`plan_research` 方法返回一个由 LLM 生成的、包含多个具体研究角度的子查询列表。**

---

## 第二步：上下文检索与处理

拿到子查询列表后，`_get_context_by_web_search` 方法会遍历这个列表，对每个子查询进行处理。

```python
# in gpt_researcher/skills/researcher.py
async def _get_context_by_web_search(self, query, scraped_data: list | None = None, ...):
    # ...
    sub_queries = await self.plan_research(query, query_domains)
    if self.researcher.report_type != "subtopic_report":
        sub_queries.append(query) # Also include the original query

    # ► Key Step 3: Process each sub-query asynchronously
    context = await asyncio.gather(
        *[
            self._process_sub_query(sub_query, scraped_data, query_domains)
            for sub_query in sub_queries
        ]
    )
    # ...
```

### 2.1 `_process_sub_query` 方法

这是处理单个子查询的“工作单元”。

```python
# in gpt_researcher/skills/researcher.py
async def _process_sub_query(self, sub_query: str, scraped_data: list = [], ...):
    # ...

    # ► Key Step 4: Skip web scraping if data is provided
    if not scraped_data:
        # This block is SKIPPED in local document mode
        scraped_data = await self._scrape_data_by_urls(sub_query, query_domains)

    # ► Key Step 5: Get relevant content from provided data
    if scraped_data:
        web_context = await self.researcher.context_manager.get_similar_content_by_query(sub_query, scraped_data)
        # ...
        return web_context
    # ...
```

**工作机制解析**:

-   该方法接收到一个子查询和 `scraped_data`（即从本地文件加载的 `document_data`）。
-   它首先检查 `scraped_data` 是否为空。在本地文档模式下，这个列表包含了所有文档的内容，因此**不为空**。
-   因此，`if not scraped_data:` 条件不满足，所有与网络爬取相关的代码（`_scrape_data_by_urls`）被**完全跳过**。
-   核心调用 `self.researcher.context_manager.get_similar_content_by_query`，将子查询和所有本地文档内容传递给它。

### 2.2 `ContextManager.get_similar_content_by_query`

这个方法是连接 `ResearchConductor` 和 `ContextCompressor` 的桥梁。

```python
# in gpt_researcher/skills/context_manager.py
class ContextManager:
    async def get_similar_content_by_query(self, query, pages):
        # ...
        # ► Key Step 6: Instantiate and use ContextCompressor
        context_compressor = ContextCompressor(
            documents=pages,
            embeddings=self.researcher.memory.get_embeddings(),
            # ...
        )
        return await context_compressor.async_get_context(
            query=query, max_results=10, # ...
        )
```

### 2.3 `ContextCompressor.async_get_context` (核心中的核心)

这里是魔法真正发生的地方，它利用了 `langchain` 的 `ContextualCompressionRetriever`。

```python
# in gpt_researcher/context/compression.py
class ContextCompressor:
    def __init__(self, documents, embeddings, ...):
        self.documents = documents # All local document contents
        self.embeddings = embeddings # The embedding model (e.g., OpenAIEmbeddings)
        # ...

    def __get_contextual_retriever(self):
        # 1. Splitter: Prepares documents by splitting them into smaller chunks.
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)

        # 2. Relevance Filter: The core of the similarity search.
        relevance_filter = EmbeddingsFilter(embeddings=self.embeddings,
                                            similarity_threshold=0.35) # Configurable threshold

        # 3. Pipeline: Chains the splitter and filter together.
        pipeline_compressor = DocumentCompressorPipeline(
            transformers=[splitter, relevance_filter]
        )

        # 4. Base Retriever: A simple retriever that just returns all provided documents.
        base_retriever = SearchAPIRetriever(
            pages=self.documents
        )

        # 5. Contextual Retriever: The final component that orchestrates everything.
        contextual_retriever = ContextualCompressionRetriever(
            base_compressor=pipeline_compressor, base_retriever=base_retriever
        )
        return contextual_retriever

    async def async_get_context(self, query, max_results=5, ...):
        # ► Key Step 7: Get the configured retriever
        compressed_docs = self.__get_contextual_retriever()

        # ► Key Step 8: Invoke the retriever with the sub-query
        relevant_docs = await asyncio.to_thread(compressed_docs.invoke, query, **self.kwargs)

        return self.prompt_family.pretty_print_docs(relevant_docs, max_results)
```

**工作机制解析**:

1.  **获取 Retriever**: `__get_contextual_retriever` 方法构建并返回一个 `ContextualCompressionRetriever`。这个 `Retriever` 是一个精密的组件，它按以下方式工作：
2.  **调用 `invoke(query)`**: 当 `compressed_docs.invoke(query)` 被调用时，`ContextualCompressionRetriever` 开始执行其内部流程：
    a.  **基础检索**: 它首先通过 `base_retriever` 获取所有的文档块（这里是全部本地文档内容）。
    b.  **压缩管道**: 然后，它将这些文档块和用户的 `query` 一起传递给 `pipeline_compressor`。
    c.  **文本分割**: `splitter` 确保所有文档都按统一的大小（1000个字符）被分割成块，以便进行有效的向量化。
    d.  **嵌入和过滤**: 这是最关键的一步。`EmbeddingsFilter` 会：
        i.  将用户的 `query`（子查询）转换成一个向量。
        ii. 将每一个文档块也转换成一个向量。
        iii. 计算 `query` 向量与每个文档块向量之间的**余弦相似度**。
        iv. **只保留**那些相似度分数**高于**预设阈值（`similarity_threshold`，例如 0.35）的文档块。
3.  **返回结果**: `invoke` 方法最终返回一个只包含与 `query` 高度相关的文档块的列表 (`relevant_docs`)。
4.  **格式化输出**: `pretty_print_docs` 将这些相关的文档块格式化成一个干净的字符串，其中包含了每个块的来源（文件名）和内容。

---

## 第三步：整合上下文

在 `_get_context_by_web_search` 的 `asyncio.gather` 完成后，`context` 变量现在是一个列表，其中每个元素都是一个子查询检索到的、高度相关的文本字符串。

```python
# in gpt_researcher/skills/researcher.py
async def _get_context_by_web_search(self, ...):
    # ...
    context = await asyncio.gather(...) # context is now a list of strings

    # ► Key Step 9: Join all context pieces
    context = [c for c in context if c] # Filter out empty results
    if context:
        combined_context = " ".join(context)
        return combined_context
    return []
```

**工作机制解析**:

-   代码首先过滤掉可能出现的空结果（如果某个子查询没有找到任何相关内容）。
-   然后，使用 `" ".join(context)` 将所有子查询找到的相关信息片段**合并成一个单一的、巨大的字符串**。
-   这个 `combined_context` 就是最终的研究上下文，它将被传递给报告生成器。

通过这个“分解-处理-合并”的流程，GPT-Researcher 能够系统地、深入地从大量本地文档中，为特定的研究任务提取出最核心、最相关的信息，为生成高质量的报告奠定了坚实的基础。
