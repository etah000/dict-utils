# 仓库指南

## 输出规范

所有文档、计划和实现说明均使用中文；代码、命令、文件名及公开 API 标识保持原文。

## 项目结构与模块组织

本仓库使用 `src/` 布局：`mdict_audio_app` 是桌面应用，`mdict_utils` 提供独立的 MDict 读取与提取工具。

- `src/mdict_utils/readmdict.py`：MDX/MDD 读取器及命令行入口。
- `src/mdict_utils/pureSalsa20.py`、`src/mdict_utils/ripemd128.py`：读取加密词典所需的密码学实现。
- `MDX.svg`、`MDD.svg`：说明两种二进制格式的结构。
- `README.md`：记录格式背景、依赖和使用示例。

新增运行时代码放入对应的 `src/` 包，命令行和维护脚本放入 `scripts/`。不要提交提取后的词典数据、受版权保护的 `.mdx`/`.mdd` 文件或临时输出。

## 构建、测试与开发命令

使用 Python 3.11+ 开发。项目依赖和打包配置记录在 `pyproject.toml`：

- `python -m mdict_utils.readmdict --help`：确认命令行程序可加载并查看参数。
- `python -m mdict_utils.readmdict -x path/to/dictionary.mdx`：使用本地测试文件执行提取。
- `python -m compileall src/mdict_utils`：快速检查语法。

读取引擎版本 1.2 创建的 MDX 文件时需安装 `python-lzo`。未安装时程序会提示 LZO 不可用，但仍可处理受支持的 zlib 压缩文件。

## 编码风格与命名约定

遵循所编辑文件的现有风格。`src/mdict_utils/readmdict.py` 使用四空格缩进；除非整体格式化，否则保留 `src/mdict_utils/ripemd128.py` 的历史制表符缩进。函数和变量使用 `snake_case`，类使用 `CamelCase`，内部辅助项以下划线开头。显式处理字节与字符串转换，避免破坏格式兼容性。目前未配置格式化器或静态检查器。

## 测试指南

仓库目前没有自动化测试套件。修改解析逻辑时，至少执行语法检查，并分别用相关 MDX、MDD 样本验证条目迭代、输出路径、压缩块和加密块。若引入测试框架，测试文件命名为 `test_*.py`，优先使用最小化的合成数据，不得提交专有词典作为夹具。

## 提交与拉取请求规范

历史提交多使用简短、祈使式摘要，例如 `implement email regcode`。每个提交只处理一个主题；涉及兼容性或格式假设时，在提交正文中说明。拉取请求应描述受影响的格式或版本，列出验证命令及结果，并关联相关议题。行为或格式认知发生变化时，同步更新 README 示例或 SVG 图示。
