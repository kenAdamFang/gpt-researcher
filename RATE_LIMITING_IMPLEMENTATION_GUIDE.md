# API速率限制解决方案实施指南

本报告提供了针对“精确报告”流程中 LLM 并发调用导致 API 速率超限问题的详细代码修改方案。该方案通过引入异步延迟，将 LLM 的调用频率严格限制在每秒1次。

---

## 1. 核心问题

在 `llm_document_filter.py` 和 `researcher.py` 中使用了 `asyncio.gather`，这会导致在短时间内并发地向 LLM API 发送大量请求，触发了“每分钟超过60次”的速率限制。

---

## 2. 解决方案：串行化异步节流

我们将用一个简单的 `for` 循环和 `await asyncio.sleep(1)` 来替换 `asyncio.gather`，将并发调用改为串行化异步调用，从而在每次调用之间强制引入1秒的延迟。

**优点**: 实现简单，绝对可靠。
**代价**: 显著增加总处理时间，是以时间换取稳定性的策略。

---

## 3. 代码修改详情

### **修改文件一：`gpt_researcher/document/llm_document_filter.py`**

**目标**: 对 `LLMDocumentFilter.filter_documents` 方法进行修改，限制 `FAST_LLM` 的调用频率。

**操作**: 请用以下代码**覆盖** `filter_documents` 方法的全部内容。

```python
# In gpt_researcher/document/llm_document_filter.py

# ... (imports and other methods remain the same) ...

    async def filter_documents(self, documents: List[Dict], query: str) -> List[str]:
        """
        Filters a list of documents, returning a list of relevant filenames.
        This version includes rate limiting to avoid overwhelming the API.
        """
        batches = self._batch_documents(documents)

        # 初始化一个空列表来收集所有批次返回的相关文件名
        all_relevant_filenames = []

        # 使用 for 循环代替 asyncio.gather 来串行处理每个批次
        for i, batch in enumerate(batches):
            print(f"Processing filter batch {i+1}/{len(batches)}...") # 添加日志以便跟踪进度

            previews = [{"filename": doc.get("metadata", {}).get("source", ""), "preview": self._get_word_preview(doc.get("raw_content", ""))} for doc in batch]
            prompt = self._create_filtering_prompt(previews, query)

            # 直接 await 单个 LLM 调用
            response = await create_chat_completion(
                model=self.cfg.fast_llm_model,
                messages=[{"role": "user", "content": prompt}],
                llm_provider=self.cfg.fast_llm_provider,
                max_tokens=512,
                llm_kwargs=self.cfg.llm_kwargs,
            )

            # 解析当前批次的响应
            batch_relevant_filenames = self._extract_json_from_response(response)
            all_relevant_filenames.extend(batch_relevant_filenames)

            # 在每次 API 调用后，强制等待1秒钟
            # 这是实现速率限制的核心
            print(f"Rate limit sleep: 1 second after batch {i+1}")
            await asyncio.sleep(1)

        # 返回所有批次结果的并集，并去重
        return list(set(all_relevant_filenames))

```
**修改说明**:
*   我们移除了 `tasks` 列表和 `asyncio.gather`。
*   引入了一个 `for` 循环来遍历 `batches`。
*   在循环内部，我们 `await` 单个 `create_chat_completion` 调用，然后将结果添加到 `all_relevant_filenames` 列表中。
*   在每次循环的末尾，我们调用 `await asyncio.sleep(1)`，确保了对 `FAST_LLM` 的调用间隔至少为1秒。

---

### **修改文件二：`gpt_researcher/skills/researcher.py`**

**目标**: 对 `ResearchConductor.conduct_research` 方法的 `local_precise` 分支进行修改，限制 `STRATEGIC_LLM` 的调用频率。

**操作**: 请用以下代码**覆盖** `conduct_research` 方法中 `elif self.researcher.report_source == "local_precise":` 分支的全部内容。

```python
# In gpt_researcher/skills/researcher.py, inside the conduct_research method

        elif self.researcher.report_source == "local_precise":
            self.logger.info("Using Precise Report with LLM-native filtering workflow.")

            all_documents = await DocumentLoader(self.researcher.cfg.doc_path).load()
            self.logger.info(f"Loaded {len(all_documents)} documents for filtering.")

            # Stage 1: LLM-based Filtering (now with rate limiting)
            llm_filter = LLMDocumentFilter(cfg=self.researcher.cfg)
            await stream_output("logs", f"Filtering {len(all_documents)} documents with FAST_LLM (1 sec delay per batch)...", self.researcher.websocket)
            relevant_filenames = await llm_filter.filter_documents(all_documents, self.researcher.query)

            top_documents = [doc for doc in all_documents if doc.get("metadata", {}).get("source") in relevant_filenames]
            self.logger.info(f"Filtered down to {len(top_documents)} most relevant documents.")

            # Stage 2: In-depth Extraction & Summarization (with rate limiting)
            await stream_output("logs", f"Extracting information from {len(top_documents)} documents with STRATEGIC_LLM (1 sec delay per call)...", self.researcher.websocket)

            # 初始化一个空列表来收集结构化的摘要数据
            structured_data = []

            # 使用 for 循环代替 asyncio.gather 来串行处理每个文档
            for i, doc in enumerate(top_documents):
                self.logger.info(f"Processing document {i+1}/{len(top_documents)} for summarization...")

                # 直接 await 单个摘要提取调用
                summary_object = await extract_and_summarize_document(doc, self.researcher.query, self.researcher.cfg)
                structured_data.append(summary_object)

                # 在每次 API 调用后，强制等待1秒钟
                self.logger.info(f"Rate limit sleep: 1 second after document {i+1}")
                await asyncio.sleep(1)

            self.logger.info(f"Generated {len(structured_data)} structured summaries.")

            # Stage 3: Context Aggregation
            await stream_output("logs", "Aggregating final context...", self.researcher.websocket)
            research_data = format_final_context(structured_data)
            self.logger.info(f"Final context aggregated. Length: {len(research_data)} chars.")

```
**修改说明**:
*   与筛选阶段的修改逻辑完全相同，我们用一个 `for` 循环替换了 `asyncio.gather`。
*   在循环中，我们逐一 `await` 对 `extract_and_summarize_document` 的调用。
*   每次调用后，同样 `await asyncio.sleep(1)`，从而将对 `STRATEGIC_LLM` 的调用频率也限制在每秒1次。
*   我们还向 `stream_output` 和日志中添加了更明确的信息，告知用户正在进行带延迟的调用。

按照本指南进行修改，即可有效解决 API 速率超限的问题，保证“精确报告”流程的稳定运行。
