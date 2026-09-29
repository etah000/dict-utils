# MDict 文件格式与音频工具

本项目包含 MDict 文件读取工具和一个离线桌面应用。读取工具分析并提取 MDX 词条和 MDD 资源；桌面应用可将词典内容导入 SQLite，按生词列表选择词头发音和例句音频，并通过 FFmpeg 生成音频文件及 JSON 清单。

## 桌面应用

桌面应用使用 Python 3.11 及以上版本，当前面向 Windows。开发环境可在 PowerShell 中安装并启动：

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m mdict_audio_app.main --data-dir data
```

`data/dictionary.db` 保存词典目录和词条索引，`data/audio/audio-*.db` 保存音频分片。应用需要 FFmpeg 和 FFprobe；打包前请提供 `bin/ffmpeg.exe`、`bin/ffprobe.exe` 和 `LICENSES/` 许可证目录，并确保 FFmpeg 工具位于开发机的 `PATH`。分发验证脚本会检查这些文件。

启动自检：

```powershell
python -m mdict_audio_app.main --self-check --data-dir data
```

应用启动时会在开始词典导入前恢复遗留导入。维护服务支持清理词典删除后遗留的孤立音频，并使用 SQLite backup API 创建包含 `dictionary.db`、全部音频分片和 `integrity.json` 的一致备份。

Windows standalone 包可用以下命令构建和检查：

```powershell
pyside6-deploy -c pysidedeploy.spec
powershell -ExecutionPolicy Bypass -File scripts/verify_distribution.ps1
python scripts/benchmark_catalog.py --entries 1000000 --audio-resources 200000
```

词典文件通常受版权保护，请勿提交 `.mdx`、`.mdd` 文件或提取出的词典数据。

## MDX 和 MDD 文件

MDX 保存词条及释义，MDD 保存词典引用的资源，例如图片、发音和样式表。两种文件使用相近的二进制结构。

### MDX 文件结构

![MDX 文件结构](images/MDX.svg)

### MDD 文件结构

![MDD 文件结构](images/MDD.svg)

## 读取和提取词典

安装项目后，可通过模块命令查看参数或提取词典：

```powershell
python -m mdict_utils.readmdict --help
python -m mdict_utils.readmdict -x path/to/dictionary.mdx
```

提取时会生成 `.txt` 词条文件；若同名 MDD 文件存在，还会将资源写入词典旁的 `data` 目录。读取由 MDict 引擎 1.2 创建的文件需要安装与当前 Python 环境兼容的 `python-lzo`。未安装时，程序仍可读取支持的 zlib 压缩块。

也可以在 Python 中迭代词条：

```python
from mdict_utils.readmdict import MDD, MDX

mdx = MDX("dictionary.mdx")
key, definition = next(mdx.items())

mdd = MDD("dictionary.mdd")
resource_path, resource_data = next(mdd.items())
```

MDX 返回的词头和释义是 UTF-8 编码的 `bytes`。MDD 返回的资源路径是 UTF-8 编码的 `bytes`，资源内容保持原始 `bytes`。

## 参考资料

MDX/MDD 文件结构和加密相关实现参考了 [writemdict](https://github.com/zhansliu/writemdict) 项目的公开研究。
