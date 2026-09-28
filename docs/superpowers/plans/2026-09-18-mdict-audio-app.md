# MDict 生词音频应用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个 Windows 单用户离线桌面应用，导入多个 MDX/MDD 词典，并按词典优先级把生词发音和限定数量的例句发音合成为单个音频文件。

**Architecture:** PySide6/Qt Widgets 只负责界面和任务调度，领域与应用服务不依赖 Qt。`dictionary.db` 保存关系与索引，`audio-NNN.db` 保存去重后的音频 BLOB；导入、查询、音频规划和 FFmpeg 渲染通过明确接口组合。

**Tech Stack:** Python 3.12、PySide6、SQLite、Beautiful Soup、FFmpeg/FFprobe、pytest、pytest-qt、Nuitka/`pyside6-deploy`。

**Spec:** `docs/superpowers/specs/2026-09-18-mdict-audio-app-design.md`

## Global Constraints

- 仅支持 64 位 Windows 10/11，单用户、离线运行，不启动网络服务。
- 目标容量为约 100 万词条、20 万以上音频资源、资源总量低于 10 GB。
- `dictionary.db` 不保存音频数据；每个 `audio-NNN.db` 的逻辑音频容量上限为 2 GiB。
- 释义和例句按词典优先级选取且不跨词典混合；单词发音允许配置向低优先级词典回退。
- 首版缺失音频直接跳过；TTS 仅通过 `AudioProvider` 接口预留扩展点。
- 所有长操作在后台线程运行，每个线程拥有独立 SQLite 连接。
- 测试只能使用合成数据，不提交专有 MDX/MDD 或提取内容。
- 新功能严格执行测试先行；每个任务通过其定向测试和全量测试后才提交。

## File Map

```text
pyproject.toml                         # 依赖、测试和打包入口
src/mdict_audio_app/
├── __init__.py
├── main.py                            # 应用组合根与入口
├── config.py                          # 路径及默认值
├── domain/models.py                   # 领域枚举和不可变数据类
├── storage/migrations.py              # dictionary.db 版本迁移
├── storage/catalog.py                 # 目录库事务和查询
├── storage/audio_store.py             # BLOB 分片、去重和读取
├── parsing/base.py                    # 解析接口及注册表
├── parsing/generic.py                 # 通用 sound:// 解析器
├── parsing/css_selector.py            # 可配置的词典专用适配器
├── importers/mdict_source.py           # readmdict 适配层
├── importers/service.py                # 批量导入编排
├── services/dictionary_query.py        # 优先级查词
├── services/audio_plan.py              # 片段选择和时长限制
├── services/audio_render.py            # FFmpeg 渲染
├── services/maintenance.py             # 删除、垃圾回收和备份
└── ui/
    ├── workers.py                      # Qt 后台任务桥接
    ├── main_window.py                  # 主窗口和页面导航
    ├── dictionary_page.py              # 词典管理
    └── audio_page.py                   # 生词预览和音频生成
tests/                                  # 与 src 结构对应的测试
scripts/verify_distribution.ps1         # 干净环境分发检查
```

---

### Task 1: 项目骨架与领域模型

**Files:**
- Create: `pyproject.toml`
- Create: `src/mdict_audio_app/__init__.py`
- Create: `src/mdict_audio_app/config.py`
- Create: `src/mdict_audio_app/domain/__init__.py`
- Create: `src/mdict_audio_app/domain/models.py`
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Produces: `normalize_headword(value: str) -> str`
- Produces: `AudioKind`、`ResolutionStatus`、`AudioSelectionSettings`、`AudioClip`、`AudioPlan`

- [ ] **Step 1: 写失败测试**

```python
from mdict_audio_app.domain.models import AudioSelectionSettings, normalize_headword


def test_normalize_headword_unifies_case_width_and_space():
    assert normalize_headword("  Ｃafé\t") == "café"


def test_selection_settings_reject_negative_limits():
    try:
        AudioSelectionSettings(max_examples=-1)
    except ValueError as exc:
        assert "max_examples" in str(exc)
    else:
        raise AssertionError("negative max_examples was accepted")
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `python -m pytest tests/domain/test_models.py -v`  
Expected: FAIL，包含 `ModuleNotFoundError: No module named 'mdict_audio_app'`。

- [ ] **Step 3: 创建项目配置和最小领域模型**

`pyproject.toml` 声明 `requires-python = ">=3.12,<3.15"`，运行依赖为 `PySide6>=6.8,<7`、`beautifulsoup4>=4.12,<5`，开发依赖为 `pytest>=8,<10`、`pytest-qt>=4.4,<5`。配置 setuptools 的 `src` 布局和 pytest 的 `pythonpath = ["src", "."]`。

```python
# src/mdict_audio_app/domain/models.py
from dataclasses import dataclass, field
from enum import StrEnum
import unicodedata


class AudioKind(StrEnum):
    HEADWORD = "HEADWORD"
    EXAMPLE = "EXAMPLE"
    UNKNOWN = "UNKNOWN"


class ResolutionStatus(StrEnum):
    MATCHED = "MATCHED"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


def normalize_headword(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@dataclass(frozen=True)
class AudioSelectionSettings:
    max_examples: int = 2
    max_total_ms: int | None = None
    clip_gap_ms: int = 300
    word_gap_ms: int = 800
    fallback_headword_audio: bool = True

    def __post_init__(self) -> None:
        if self.max_examples < 0:
            raise ValueError("max_examples must be non-negative")


@dataclass(frozen=True)
class AudioClip:
    word: str
    kind: AudioKind
    resource_id: int
    duration_ms: int
    dictionary_id: int
    example_text: str | None = None


@dataclass(frozen=True)
class AudioPlan:
    clips: tuple[AudioClip, ...] = field(default_factory=tuple)
    skipped: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    total_audio_ms: int = 0
```

Run: `python -m pip install -e ".[dev]"`  
Expected: 项目及开发依赖安装成功。

- [ ] **Step 4: 运行定向测试和全量测试**

Run: `python -m pytest tests/domain/test_models.py -v`  
Run: `python -m pytest -q`  
Expected: 两个测试 PASS，全量测试无失败。

- [ ] **Step 5: 提交**

```powershell
git add pyproject.toml src/mdict_audio_app tests/domain
git commit -m "feat: scaffold dictionary audio application"
```

### Task 2: 目录数据库迁移与事务接口

**Files:**
- Create: `src/mdict_audio_app/storage/__init__.py`
- Create: `src/mdict_audio_app/storage/migrations.py`
- Create: `src/mdict_audio_app/storage/catalog.py`
- Create: `tests/conftest.py`
- Test: `tests/storage/test_catalog.py`

**Interfaces:**
- Consumes: `AudioKind`、`ResolutionStatus`、`normalize_headword`
- Produces: `Catalog.open(path: Path) -> Catalog`
- Produces: `CatalogFactory(path: Path).open() -> Catalog`
- Produces: `Catalog.transaction()`、`create_dictionary(...) -> int`、`insert_entry(...) -> int`
- Produces: `register_audio(...) -> int`、`resolve_audio_link(...)`
- Produces: `list_dictionaries()`、`set_dictionary_priorities(ids: Sequence[int])`

- [ ] **Step 1: 写迁移失败测试**

```python
from mdict_audio_app.storage.catalog import Catalog


def test_open_creates_versioned_schema(tmp_path):
    catalog = Catalog.open(tmp_path / "dictionary.db")
    tables = catalog.table_names()
    assert {"dictionary", "entry", "example", "audio_resource", "audio_link", "audio_shard"} <= tables
    assert catalog.schema_version() == 1
    catalog.close()
```

- [ ] **Step 2: 确认测试因 `Catalog` 不存在而失败**

Run: `python -m pytest tests/storage/test_catalog.py::test_open_creates_versioned_schema -v`  
Expected: FAIL，导入 `mdict_audio_app.storage.catalog` 失败。

- [ ] **Step 3: 实现版本 1 迁移**

在 `migrations.py` 中定义不可变迁移列表。版本 1 必须完整创建以下约束：

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);
CREATE TABLE dictionary (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, priority INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  source_mdx_name TEXT NOT NULL, source_mdd_name TEXT,
  mdx_sha256 BLOB NOT NULL UNIQUE, mdd_sha256 BLOB,
  metadata_json TEXT NOT NULL, adapter_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('IMPORTING','READY','FAILED')),
  imported_at TEXT NOT NULL
);
CREATE TABLE entry (
  id INTEGER PRIMARY KEY, dictionary_id INTEGER NOT NULL REFERENCES dictionary(id) ON DELETE CASCADE,
  headword TEXT NOT NULL, normalized_headword TEXT NOT NULL,
  definition_html TEXT NOT NULL, definition_text TEXT NOT NULL, source_order INTEGER NOT NULL
);
CREATE INDEX entry_lookup_idx ON entry(normalized_headword, dictionary_id, source_order);
CREATE TABLE example (
  id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  text TEXT NOT NULL, source_order INTEGER NOT NULL
);
CREATE TABLE audio_shard (
  id INTEGER PRIMARY KEY, filename TEXT NOT NULL UNIQUE, byte_size INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK(status IN ('WRITABLE','SEALED'))
);
CREATE TABLE audio_resource (
  id INTEGER PRIMARY KEY, sha256 BLOB NOT NULL UNIQUE, media_type TEXT NOT NULL,
  duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0), byte_size INTEGER NOT NULL,
  shard_id INTEGER NOT NULL REFERENCES audio_shard(id), blob_id INTEGER NOT NULL,
  UNIQUE(shard_id, blob_id)
);
CREATE TABLE audio_link (
  id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  example_id INTEGER REFERENCES example(id) ON DELETE CASCADE,
  resource_id INTEGER REFERENCES audio_resource(id), kind TEXT NOT NULL,
  resolution_status TEXT NOT NULL, source_ref TEXT NOT NULL, source_order INTEGER NOT NULL
);
```

`Catalog.open()` 设置 `PRAGMA foreign_keys=ON`、`PRAGMA journal_mode=WAL`、`PRAGMA busy_timeout=5000`，只顺序执行尚未应用的迁移。

- [ ] **Step 4: 增加事务回滚测试并实现仓储方法**

```python
def test_transaction_rolls_back_all_rows(catalog):
    try:
        with catalog.transaction():
            catalog.create_dictionary(name="A", mdx_sha256=b"a" * 32, source_mdx_name="a.mdx")
            raise RuntimeError("stop")
    except RuntimeError:
        pass
    assert catalog.count("dictionary") == 0
```

实现参数化 SQL，不允许拼接用户输入；`create_dictionary` 自动分配下一优先级并写入 UTC ISO 8601 时间。

`tests/conftest.py` 提供 `catalog(tmp_path)` fixture：每次测试创建独立 `dictionary.db`，在 `yield` 后关闭连接。后续存储、服务和 UI 测试复用该 fixture；任务特有的假来源、假探针和假命令执行器留在各自测试模块中定义。

- [ ] **Step 5: 运行存储测试和全量测试**

Run: `python -m pytest tests/storage/test_catalog.py -v`  
Run: `python -m pytest -q`  
Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add src/mdict_audio_app/storage tests/storage
git commit -m "feat: add versioned dictionary catalog"
```

### Task 3: 音频 BLOB 分片与全局去重

**Files:**
- Create: `src/mdict_audio_app/storage/audio_store.py`
- Test: `tests/storage/test_audio_store.py`

**Interfaces:**
- Consumes: `Catalog`
- Produces: `AudioBlobStore.put(data: bytes, media_type: str, duration_ms: int) -> int`
- Produces: `AudioBlobStore.read(resource_id: int) -> bytes`
- Produces: `AudioBlobStore.delete_unreferenced() -> int`

- [ ] **Step 1: 写去重和分片失败测试**

```python
def test_put_deduplicates_and_rotates(catalog, tmp_path):
    store = AudioBlobStore(tmp_path, catalog, max_shard_bytes=6)
    first = store.put(b"abc", "audio/mpeg", 100)
    duplicate = store.put(b"abc", "audio/mpeg", 100)
    second = store.put(b"defg", "audio/wav", 200)
    assert first == duplicate
    assert store.read(first) == b"abc"
    assert catalog.audio_location(first).shard_id != catalog.audio_location(second).shard_id
```

- [ ] **Step 2: 确认测试因 `AudioBlobStore` 不存在而失败**

Run: `python -m pytest tests/storage/test_audio_store.py::test_put_deduplicates_and_rotates -v`  
Expected: FAIL，导入类失败。

- [ ] **Step 3: 实现分片写入顺序和读取**

`put()` 计算 SHA-256，先查 `audio_resource.sha256`；命中时直接返回现有 ID。未命中时选择逻辑容量足够的 `WRITABLE` 分片，否则封存旧分片并创建 `audio-%03d.db`。分片库使用：

```sql
CREATE TABLE IF NOT EXISTS audio_blob (
  id INTEGER PRIMARY KEY,
  sha256 BLOB NOT NULL UNIQUE,
  data BLOB NOT NULL
);
```

先提交 BLOB，再调用 `Catalog.register_audio()` 提交目录记录；若目录提交失败，保留可回收的孤立 BLOB。所有文件路径通过 `Path.resolve()` 验证仍位于存储根目录。

- [ ] **Step 4: 写孤立 BLOB 清理测试并实现清理**

```python
def test_delete_unreferenced_removes_catalog_and_blob(catalog, store):
    resource_id = store.put(b"orphan", "audio/mpeg", 50)
    assert store.delete_unreferenced() == 1
    assert catalog.find_audio(resource_id) is None
```

删除顺序为先删除目录记录并提交，再删除对应分片行；失败的物理删除记录到返回的维护报告，下一次可重试。

- [ ] **Step 5: 运行测试并提交**

Run: `python -m pytest tests/storage -v`  
Run: `python -m pytest -q`  
Expected: PASS。

```powershell
git add src/mdict_audio_app/storage/audio_store.py tests/storage/test_audio_store.py
git commit -m "feat: store audio in deduplicated sqlite shards"
```

### Task 4: 通用 HTML 解析器与适配器注册表

**Files:**
- Create: `src/mdict_audio_app/parsing/__init__.py`
- Create: `src/mdict_audio_app/parsing/base.py`
- Create: `src/mdict_audio_app/parsing/generic.py`
- Create: `src/mdict_audio_app/parsing/css_selector.py`
- Test: `tests/parsing/test_generic.py`
- Test: `tests/parsing/test_css_selector.py`

**Interfaces:**
- Produces: `AudioReference(source_ref, kind, example_index)`
- Produces: `ParsedEntry(headword, definition_html, definition_text, examples, audio_refs)`
- Produces: `ParserAdapter.parse(headword: str, html: str) -> ParsedEntry`
- Produces: `AdapterRegistry.select(metadata: Mapping[str, str], requested: str | None) -> ParserAdapter`
- Produces: `CssSelectorAdapter(name, title_pattern, definition_selector, example_selector, headword_audio_selector, example_audio_selector)`

- [ ] **Step 1: 写通用引用提取失败测试**

```python
def test_generic_parser_extracts_and_normalizes_sound_refs():
    parsed = GenericParser().parse(
        "Hello",
        '<div>Hello</div><a href="sound://Audio%2FHELLO.MP3?x=1">play</a>',
    )
    assert parsed.definition_text == "Hello play"
    assert [(r.source_ref, r.kind) for r in parsed.audio_refs] == [
        ("audio/hello.mp3", AudioKind.UNKNOWN)
    ]
```

- [ ] **Step 2: 确认测试因解析器不存在而失败**

Run: `python -m pytest tests/parsing/test_generic.py -v`  
Expected: FAIL。

- [ ] **Step 3: 实现接口、路径规范化和通用解析**

使用 `BeautifulSoup(html, "html.parser")` 提取纯文本；用大小写不敏感正则提取 `sound://`，再执行 URL 解码、反斜杠转正斜杠、去除开头 `/`、查询串和锚点，最后 `casefold()`。同一 HTML 属性只能生成一个引用。

```python
class ParserAdapter(Protocol):
    name: str
    def matches(self, metadata: Mapping[str, str]) -> bool: ...
    def parse(self, headword: str, html: str) -> ParsedEntry: ...
```

注册表优先使用显式 `requested`；否则按注册顺序选择首个 `matches=True` 的专用适配器，最终回退 `GenericParser`。

- [ ] **Step 4: 增加可配置 CSS 适配器及契约测试**

创建 `CssSelectorAdapter`，通过构造参数指定释义、例句、单词发音和例句发音选择器。测试 HTML 使用 `.definition`、`.example`、`.headword-audio`；断言例句内部引用标记为 `EXAMPLE` 且 `example_index=0`，词头引用标记为 `HEADWORD`。`matches()` 对 MDict 标题执行完整正则匹配，使首版可以用配置增加真实词典规则，而无需改动导入服务。

- [ ] **Step 5: 运行并提交**

Run: `python -m pytest tests/parsing -v`  
Run: `python -m pytest -q`  
Expected: PASS。

```powershell
git add src/mdict_audio_app/parsing tests/parsing
git commit -m "feat: add extensible mdict html parsing"
```

### Task 5: MDict 来源适配层与可恢复导入服务

**Files:**
- Create: `src/mdict_audio_app/importers/__init__.py`
- Create: `src/mdict_audio_app/importers/mdict_source.py`
- Create: `src/mdict_audio_app/importers/service.py`
- Test: `tests/importers/test_import_service.py`
- Test: `tests/importers/test_mdict_source.py`

**Interfaces:**
- Consumes: `MDX`、`MDD` from `readmdict.py`，`Catalog`、`AudioBlobStore`、`AdapterRegistry`
- Produces: `MDictSource.iter_entries()`、`iter_resources(wanted_paths)`、`metadata`
- Produces: `AudioProbe.probe(data: bytes, suffix: str) -> AudioMetadata`
- Produces: `FFprobeAudioProbe(ffprobe_path)`
- Produces: `ImportRequest`、`ImportProgress`、`ImportResult`
- Produces: `ImportService.run(request, progress_callback, cancel_event) -> ImportResult`

- [ ] **Step 1: 用内存假来源写导入失败测试**

```python
def test_import_links_only_referenced_audio(catalog, audio_store, fake_source, registry):
    fake_source.entries = [("hello", '<a href="sound://a.mp3">x</a>')]
    fake_source.resources = [("a.mp3", b"audio"), ("unused.jpg", b"image")]
    result = ImportService(catalog, audio_store, registry).run(
        ImportRequest(name="D", mdx_path="d.mdx", mdd_path="d.mdd", source=fake_source)
    )
    assert result.entries == 1
    assert result.audio_resources == 1
    assert catalog.dictionary_status(result.dictionary_id) == "READY"
```

- [ ] **Step 2: 确认测试因导入服务不存在而失败**

Run: `python -m pytest tests/importers/test_import_service.py -v`  
Expected: FAIL。

- [ ] **Step 3: 实现来源协议和 `readmdict` 适配器**

`MDictSource` 将 `bytes` 按 UTF-8 容错解码；`iter_resources(wanted_paths)` 顺序迭代 MDD，仅返回规范化路径存在于 `wanted_paths` 的资源。没有 MDD 时返回空迭代器。源文件 SHA-256 以 1 MiB 块流式计算。用依赖注入使导入测试不需要真实词典。

`FFprobeAudioProbe` 把单个 BLOB 写入临时文件，以 `ffprobe -v error -show_entries format=duration,format_name -of json` 获取格式和毫秒时长，最后删除临时文件。测试注入返回固定 `AudioMetadata` 的假探针；扩展名不在 `.mp3/.wav/.ogg/.oga/.spx/.opus/.m4a/.aac/.flac` 或 FFprobe 无法解码时，不写入 BLOB，并把链接保留为 `MISSING`。

- [ ] **Step 4: 实现两阶段导入**

第一阶段每 1,000 个词条提交一次 `entry`、`example` 和 `audio_link(resource_id=NULL)`；收集规范化引用。第二阶段扫描 MDD、经探针验证后写 BLOB、用 `source_ref` 更新为 `MATCHED`；结束时把剩余引用更新为 `MISSING`，无法分类但资源存在的链接保持 `kind=UNKNOWN`。成功设置 `READY`；异常回滚当前批次并设置 `FAILED`；取消在批次边界生效。

进度事件固定为：`HASHING`、`PARSING_MDX`、`SCANNING_MDD`、`FINALIZING`，并携带 `completed`、可空 `total` 和中文消息。

- [ ] **Step 5: 增加失败与取消测试**

验证解析异常把词典标为 `FAILED`；预置 `cancel_event` 时不再读取下一批，返回 `cancelled=True` 且词典不可查询。

- [ ] **Step 6: 运行并提交**

Run: `python -m pytest tests/importers -v`  
Run: `python -m pytest -q`  
Expected: PASS。

```powershell
git add src/mdict_audio_app/importers tests/importers
git commit -m "feat: import mdict entries and referenced audio"
```

### Task 6: 词典优先级查询服务

**Files:**
- Create: `src/mdict_audio_app/services/__init__.py`
- Create: `src/mdict_audio_app/services/dictionary_query.py`
- Test: `tests/services/test_dictionary_query.py`

**Interfaces:**
- Produces: `DictionaryQueryService.lookup(word: str, fallback_headword_audio: bool) -> WordSelection | None`
- Produces: `WordSelection(word, dictionary_id, entries, examples, headword_audio)`

- [ ] **Step 1: 写优先级行为失败测试**

```python
def test_lookup_keeps_definition_and_examples_in_highest_priority_dictionary(seeded_catalog):
    selection = DictionaryQueryService(seeded_catalog).lookup("Word", True)
    assert selection.dictionary_id == seeded_catalog.high_priority_id
    assert {e.dictionary_id for e in selection.entries} == {seeded_catalog.high_priority_id}
    assert {e.dictionary_id for e in selection.examples} == {seeded_catalog.high_priority_id}
    assert selection.headword_audio.dictionary_id == seeded_catalog.low_priority_id
```

- [ ] **Step 2: 确认测试因查询服务不存在而失败**

Run: `python -m pytest tests/services/test_dictionary_query.py -v`  
Expected: FAIL。

- [ ] **Step 3: 实现单次查词查询**

先用 `normalized_headword` 联结 `dictionary`，过滤 `enabled=1 AND status='READY'`，按 `priority, source_order` 选定首个词典；加载该词典所有同形条目的释义和例句。单词发音先查选定词典的 `HEADWORD/MATCHED`；允许回退时再按优先级查询其他词典，但不加载它们的释义和例句。

- [ ] **Step 4: 增加禁用词典、缺词和禁止回退测试并运行**

Run: `python -m pytest tests/services/test_dictionary_query.py -v`  
Run: `python -m pytest -q`  
Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add src/mdict_audio_app/services tests/services/test_dictionary_query.py
git commit -m "feat: query entries by dictionary priority"
```

### Task 7: 生词列表与音频计划

**Files:**
- Create: `src/mdict_audio_app/services/audio_plan.py`
- Test: `tests/services/test_audio_plan.py`

**Interfaces:**
- Consumes: `DictionaryQueryService`、`AudioSelectionSettings`、`AudioClip`、`AudioPlan`
- Produces: `parse_word_list(text: str) -> tuple[str, ...]`
- Produces: `parse_word_file(path: Path) -> tuple[str, ...]`
- Produces: `AudioProvider.resolve(word: str, settings: AudioSelectionSettings) -> WordSelection | None`
- Produces: `DictionaryAudioProvider`、`AudioPlanBuilder.build(words, settings) -> AudioPlan`

- [ ] **Step 1: 写去重、例句数量和全局时长失败测试**

```python
def test_plan_preserves_order_limits_examples_and_duration(query_service):
    words = parse_word_list("Alpha\nbeta\nALPHA\n")
    plan = AudioPlanBuilder(DictionaryAudioProvider(query_service)).build(
        words,
        AudioSelectionSettings(max_examples=1, max_total_ms=2500),
    )
    assert words == ("Alpha", "beta")
    assert [clip.word for clip in plan.clips] == ["Alpha", "Alpha"]
    assert sum(clip.kind is AudioKind.EXAMPLE for clip in plan.clips) == 1
    assert plan.total_audio_ms <= 2500
    assert ("beta", "超过最大总时长") in plan.skipped
```

- [ ] **Step 2: 确认测试因计划服务不存在而失败**

Run: `python -m pytest tests/services/test_audio_plan.py -v`  
Expected: FAIL。

- [ ] **Step 3: 实现确定性选择算法**

定义 `AudioProvider` Protocol，`DictionaryAudioProvider` 封装查询服务。`AudioPlanBuilder` 只依赖 Provider；以后接入 TTS 时可用组合 Provider，不改变计划或渲染器。逐词解析：加入可用单词发音，再按 `source_order` 加入至多 `max_examples` 条 `EXAMPLE/MATCHED` 音频。每次加入前计算音频时长与适用的片段/词间静音；超过全局上限时停止并记录中文原因。无词条、无音频和缺失引用分别记录不同原因。

`parse_word_file()` 对 `.txt` 按行读取；对 `.csv` 使用标准库 `csv` 读取第一列，识别 UTF-8 BOM。两种输入都调用 `parse_word_list()`，按规范化值去重但保留首次出现的原文和顺序。

- [ ] **Step 4: 增加无限时长、零例句和完全缺失测试并运行**

Run: `python -m pytest tests/services/test_audio_plan.py -v`  
Run: `python -m pytest -q`  
Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add src/mdict_audio_app/services/audio_plan.py tests/services/test_audio_plan.py
git commit -m "feat: build duration-limited audio plans"
```

### Task 8: FFmpeg 渲染与 JSON 清单

**Files:**
- Create: `src/mdict_audio_app/services/audio_render.py`
- Test: `tests/services/test_audio_render.py`
- Test: `tests/integration/test_ffmpeg_render.py`

**Interfaces:**
- Consumes: `AudioPlan`、`AudioBlobStore`
- Produces: `CommandRunner.run(args: Sequence[str]) -> None`
- Produces: `AudioRenderer.render(plan, output_path, output_format, cancel_event) -> RenderResult`

- [ ] **Step 1: 写命令构造失败测试**

```python
def test_render_materializes_blobs_and_writes_manifest(tmp_path, store, fake_runner, plan):
    result = AudioRenderer(store, fake_runner, ffmpeg="ffmpeg").render(
        plan, tmp_path / "lesson.mp3", "mp3"
    )
    assert result.output_path.name == "lesson.mp3"
    assert result.manifest_path.read_text("utf-8").find('"word": "hello"') > 0
    assert any("-ar" in command and "44100" in command for command in fake_runner.commands)
```

- [ ] **Step 2: 确认测试因渲染器不存在而失败**

Run: `python -m pytest tests/services/test_audio_render.py -v`  
Expected: FAIL。

- [ ] **Step 3: 实现安全的临时目录和确定性 FFmpeg 命令**

为每个片段从 BLOB 写入任务临时目录，分别转为 44.1 kHz、双声道、16-bit PCM WAV；对 300 ms 和 800 ms 静音各生成一个可复用 WAV。生成 concat 清单后输出到同目录临时目标：MP3 使用 `libmp3lame -b:a 128k`，M4A 使用 `aac -b:a 128k`，WAV 使用 `pcm_s16le`。成功后用 `Path.replace()` 原子替换最终文件；任何路径都不直接拼接进 shell 字符串，`subprocess.run(..., shell=False, check=True)`。

- [ ] **Step 4: 实现清单和取消语义**

JSON 使用 UTF-8、`ensure_ascii=False`，记录设置、片段顺序、词典 ID、时长和跳过原因。每个 FFmpeg 调用前检查取消事件；异常或取消时删除临时目录且不覆盖旧目标。

- [ ] **Step 5: 增加真实 FFmpeg 集成测试**

用 FFmpeg `lavfi sine` 生成两个 100 ms 合成 WAV，渲染后调用 FFprobe，断言输出可解码且时长包含配置静音。若 `shutil.which("ffmpeg")` 或 `ffprobe` 为空，用 `pytest.mark.skip` 明确跳过。

- [ ] **Step 6: 运行并提交**

Run: `python -m pytest tests/services/test_audio_render.py -v`  
Run: `python -m pytest tests/integration/test_ffmpeg_render.py -v`  
Run: `python -m pytest -q`  
Expected: 单元测试 PASS；安装 FFmpeg 时集成测试 PASS，否则只显示预期 SKIP。

```powershell
git add src/mdict_audio_app/services/audio_render.py tests/services/test_audio_render.py tests/integration
git commit -m "feat: render audio plans with ffmpeg"
```

### Task 9: Qt 应用组合根与后台任务

**Files:**
- Create: `src/mdict_audio_app/ui/__init__.py`
- Create: `src/mdict_audio_app/ui/workers.py`
- Create: `src/mdict_audio_app/ui/main_window.py`
- Create: `src/mdict_audio_app/main.py`
- Test: `tests/ui/test_workers.py`
- Test: `tests/ui/test_main_window.py`

**Interfaces:**
- Produces: `TaskWorker(fn)` signals `progress(object)`、`succeeded(object)`、`failed(str)`、`finished()`
- Produces: `create_application(data_dir: Path) -> tuple[QApplication, MainWindow]`

- [ ] **Step 1: 写后台任务信号失败测试**

```python
def test_worker_emits_result_without_blocking(qtbot):
    worker = TaskWorker(lambda progress, cancel: 42)
    with qtbot.waitSignal(worker.signals.succeeded, timeout=1000) as signal:
        QThreadPool.globalInstance().start(worker)
    assert signal.args == [42]
```

- [ ] **Step 2: 确认测试因 UI 模块不存在而失败**

Run: `python -m pytest tests/ui/test_workers.py -v`  
Expected: FAIL。

- [ ] **Step 3: 实现工作对象和主窗口骨架**

`TaskWorker` 继承 `QRunnable`，持有 `threading.Event`；捕获异常并把格式化错误发送到 UI，不在线程间传递 SQLite 连接。`MainWindow` 使用左侧导航和 `QStackedWidget`，创建“词典管理”“生词与音频”两个空页面容器。

- [ ] **Step 4: 实现组合根**

`create_application()` 创建数据目录、迁移目录库，并构造 `CatalogFactory` 而不是共享连接；把服务工厂注入页面。`main()` 只负责日志、异常钩子、显示窗口和 `app.exec()`。

- [ ] **Step 5: 运行并提交**

Run: `python -m pytest tests/ui -v`  
Run: `python -m pytest -q`  
Expected: PASS；测试使用 `QT_QPA_PLATFORM=offscreen`。

```powershell
git add src/mdict_audio_app/ui src/mdict_audio_app/main.py tests/ui
git commit -m "feat: add responsive pyside application shell"
```

### Task 10: 词典管理页面

**Files:**
- Create: `src/mdict_audio_app/ui/dictionary_page.py`
- Modify: `src/mdict_audio_app/ui/main_window.py`
- Modify: `src/mdict_audio_app/storage/catalog.py`
- Test: `tests/ui/test_dictionary_page.py`

**Interfaces:**
- Consumes: `ImportService` 工厂、`TaskWorker`、`Catalog` 查询接口
- Produces: `DictionaryPage.refresh()`、`start_import(paths)`、`set_priority(ids)`、`delete_selected()`

- [ ] **Step 1: 写优先级拖动和导入状态失败测试**

```python
def test_priority_drop_persists_order(qtbot, page, catalog):
    page.set_priority([catalog.second_id, catalog.first_id])
    assert catalog.dictionary_priorities() == [catalog.second_id, catalog.first_id]


def test_import_disables_button_until_worker_finishes(qtbot, page):
    page.start_import([Path("sample.mdx")])
    assert not page.import_button.isEnabled()
    page.on_import_finished()
    assert page.import_button.isEnabled()
```

- [ ] **Step 2: 确认测试失败并实现页面**

Run: `python -m pytest tests/ui/test_dictionary_page.py -v`  
Expected: FAIL。

页面使用 `QTableView` 和自定义 `QAbstractTableModel`，列为名称、优先级、词条数、音频数、状态、导入时间。文件对话框允许多选 MDX；自动匹配同名 MDD，未匹配时弹出可跳过的 MDD 文件选择框。每个导入任务串行排队。进度栏显示阶段和计数；取消按钮设置工作对象事件。删除前展示词条/音频链接数量，确认后调用维护服务。`Catalog.set_dictionary_priorities()` 在一个事务中把拖动后的 ID 顺序重写为连续整数。

- [ ] **Step 3: 增加失败提示和刷新测试**

注入失败的假导入服务，断言错误消息包含文件名和原因，页面仍恢复按钮并显示 `FAILED` 状态。

- [ ] **Step 4: 运行并提交**

Run: `python -m pytest tests/ui/test_dictionary_page.py -v`  
Run: `python -m pytest -q`  
Expected: PASS。

```powershell
git add src/mdict_audio_app/ui tests/ui/test_dictionary_page.py
git commit -m "feat: add dictionary management interface"
```

### Task 11: 生词预览与音频生成页面

**Files:**
- Create: `src/mdict_audio_app/ui/audio_page.py`
- Modify: `src/mdict_audio_app/ui/main_window.py`
- Test: `tests/ui/test_audio_page.py`

**Interfaces:**
- Consumes: `parse_word_list`、`AudioPlanBuilder`、`AudioRenderer`、`TaskWorker`
- Produces: `AudioPage.build_preview()`、`start_render(output_path)`

- [ ] **Step 1: 写设置映射和预览失败测试**

```python
def test_preview_maps_controls_to_selection_settings(qtbot, page, fake_plan_builder):
    page.words_edit.setPlainText("hello\nworld")
    page.max_examples_spin.setValue(3)
    page.max_minutes_spin.setValue(10)
    page.build_preview()
    settings = fake_plan_builder.last_settings
    assert settings.max_examples == 3
    assert settings.max_total_ms == 600_000
```

- [ ] **Step 2: 确认测试失败并实现页面**

Run: `python -m pytest tests/ui/test_audio_page.py -v`  
Expected: FAIL。

页面提供文本粘贴、TXT/CSV 导入、最大例句数、最大分钟数、发音回退、两类静音、MP3/M4A/WAV、输出路径和生成按钮。预览表显示生词、命中词典、单词发音、例句数、预计时长及跳过原因；预览和渲染都在工作线程运行。

- [ ] **Step 3: 增加输出保护和取消测试**

验证未预览时禁止生成；目标已存在时必须确认；取消后不替换目标，页面展示“已取消”而不是错误。

- [ ] **Step 4: 运行并提交**

Run: `python -m pytest tests/ui/test_audio_page.py -v`  
Run: `python -m pytest -q`  
Expected: PASS。

```powershell
git add src/mdict_audio_app/ui tests/ui/test_audio_page.py
git commit -m "feat: add word list and audio generation interface"
```

### Task 12: 维护、备份、端到端验收与 Windows 分发

**Files:**
- Create: `src/mdict_audio_app/services/maintenance.py`
- Modify: `src/mdict_audio_app/main.py`
- Modify: `pyproject.toml`
- Create: `pysidedeploy.spec`
- Create: `scripts/verify_distribution.ps1`
- Create: `scripts/benchmark_catalog.py`
- Create: `tests/integration/test_end_to_end.py`
- Create: `tests/services/test_maintenance.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `Catalog`、`AudioBlobStore`、导入/查询/计划/渲染服务
- Produces: `MaintenanceService.integrity_check()`、`delete_dictionary(id)`、`backup(destination)`、`recover_interrupted_imports()`

- [ ] **Step 1: 写备份一致性失败测试**

```python
def test_backup_contains_catalog_and_every_registered_shard(tmp_path, maintenance):
    destination = tmp_path / "backup"
    report = maintenance.backup(destination)
    assert (destination / "dictionary.db").is_file()
    assert {p.name for p in destination.glob("audio-*.db")} == set(report.shard_files)
    assert report.integrity_ok
```

- [ ] **Step 2: 确认测试失败并实现维护服务**

Run: `python -m pytest tests/services/test_maintenance.py -v`  
Expected: FAIL。

备份使用 SQLite backup API 获取一致快照，逐片复制到临时备份目录并执行 `PRAGMA integrity_check`，全部通过后原子重命名。启动恢复把遗留 `IMPORTING` 标为 `FAILED` 并清理任务临时目录。删除词典后执行无引用音频垃圾回收；分片压缩只通过用户触发的维护操作执行。

- [ ] **Step 3: 写并运行端到端合成测试**

测试使用假 MDict 来源和两段 FFmpeg 合成音频：导入两个优先级不同的词典，确认释义来自高优先级词典、缺失单词发音从低优先级回退、例句不跨词典，生成文件和 JSON 清单后通过 FFprobe。没有 FFmpeg 时只跳过渲染断言，导入到计划阶段仍必须通过。

Run: `python -m pytest tests/integration/test_end_to_end.py -v`  
Expected: PASS，或仅 FFmpeg 部分显示预期 SKIP。

- [ ] **Step 4: 配置分发和启动脚本**

在 `pyproject.toml` 添加入口 `mdict-audio = "mdict_audio_app.main:main"`。`pysidedeploy.spec` 使用 `standalone`，只包含 Qt Core/Gui/Widgets/Multimedia，排除 QML、WebEngine、Charts，并把 `bin/ffmpeg.exe`、`bin/ffprobe.exe` 和许可证目录加入分发。

`scripts/verify_distribution.ps1` 必须：启动应用的 `--self-check`；检查 Qt、SQLite 和 FFmpeg；创建临时数据目录；运行合成导入到音频计划的 smoke test；任何步骤失败返回非零退出码。

- [ ] **Step 5: 更新用户文档**

README 增加 Python 3.12 环境、开发安装、测试、启动、数据目录、FFmpeg 来源与许可证、备份恢复和打包命令：

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m mdict_audio_app.main
pyside6-deploy -c pysidedeploy.spec
```

- [ ] **Step 6: 执行完整验证**

Run: `python -m pytest -q`  
Expected: 所有非条件测试 PASS，无非预期 warning。

Run: `python -m compileall -q src readmdict.py pureSalsa20.py ripemd128.py`  
Expected: exit 0。

Run: `pyside6-deploy -c pysidedeploy.spec`  
Expected: 生成 Windows standalone 分发目录。

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify_distribution.ps1`  
Expected: 输出 `Distribution verification passed`，exit 0。

Run: `python scripts/benchmark_catalog.py --entries 1000000 --audio-resources 200000`  
Expected: 使用合成行完成导入；进程峰值常驻内存低于 1 GiB；随机抽取 1,000 个精确词头查询，其热缓存 P95 低于 100 ms。脚本输出 JSON 报告，失败任一阈值时返回非零退出码。

- [ ] **Step 7: 在干净 Windows 虚拟机中验收**

复制分发目录到未安装 Python/Qt 的 Windows 10/11 虚拟机；导入合成词典、调整优先级、生成 MP3、关闭并重启、执行备份与恢复。验收记录保存到发布说明，所有操作成功后才发布。

- [ ] **Step 8: 提交**

```powershell
git add src/mdict_audio_app pyproject.toml pysidedeploy.spec scripts tests README.md
git commit -m "feat: complete offline mdict audio workflow"
```
