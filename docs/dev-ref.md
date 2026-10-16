# 开发参考

## 开发环境

项目使用 Python 3.11 及以上版本。Windows PowerShell 中安装开发依赖并启动应用：

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m mdict_audio_app.main --data-dir data
```

启动自检：

```powershell
python -m mdict_audio_app.main --self-check --data-dir data
```

应用启动时会在开始词典导入前恢复遗留导入。维护服务支持清理词典删除后遗留的孤立音频，并使用 SQLite backup API 创建包含 `dictionary.db`、全部音频分片和 `integrity.json` 的一致备份。

`data/dictionary.db` 保存词典目录和词条索引，`data/audio/audio-*.db` 保存音频分片。

## Windows standalone 打包

打包前请提供 `bin/ffmpeg.exe`、`bin/ffprobe.exe` 和 `LICENSES/` 许可证目录，并确保 FFmpeg 工具位于开发机的 `PATH`。分发验证脚本会检查这些文件。

```powershell
pyside6-deploy -c pysidedeploy.spec
powershell -ExecutionPolicy Bypass -File scripts/verify_distribution.ps1
python scripts/benchmark_catalog.py --entries 1000000 --audio-resources 200000
```

## MDX 和 MDD 文件结构

MDX 保存词条及释义，MDD 保存词典引用的资源，例如图片、发音和样式表。两种文件使用相近的二进制结构。

### MDX 文件结构

![MDX 文件结构](../images/MDX.svg)

### MDD 文件结构

![MDD 文件结构](../images/MDD.svg)

MDX/MDD 文件结构和加密相关实现参考了 [writemdict](https://github.com/zhansliu/writemdict) 项目的公开研究。
