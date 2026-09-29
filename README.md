An Analysis of MDX/MDD File Format
==================================

    MDict is a multi-platform open dictionary
    
which are both questionable. It is not available for every platform, e.g. OS X, Linux.
Its  dictionary file format is not open. But this has not hindered its popularity,
and many dictionaries have been created for it.

This is an attempt to reveal MDX/MDD file format, so that my favorite dictionaries,
created by MDict users, could be used elsewhere.


MDict Audio 应用
================

仓库中的 `mdict_audio_app` 是面向 Windows 的离线 Python 3.12 桌面应用：它把多个
MDX/MDD 词典导入 SQLite，按生词列表选择词头和例句音频，并通过 FFmpeg 生成音频及
JSON 清单。音频以内容寻址的 SQLite 分片保存，不会为每个资源创建单独文件。

开发环境
--------

在 PowerShell 中执行：

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m mdict_audio_app.main --data-dir data
```

`data/dictionary.db` 保存目录和词条索引，`data/audio/audio-*.db` 保存音频分片。
请在打包前提供 `bin/ffmpeg.exe`、`bin/ffprobe.exe` 和 `LICENSES/` 许可证目录，并确保
FFmpeg 工具也位于开发机的 `PATH`；验证脚本会对缺失项给出明确错误。词典文件通常受
版权保护，不应提交到仓库。

维护、备份与打包
----------------

启动自检使用 `python -m mdict_audio_app.main --self-check --data-dir data`。应用启动阶段（在
UI 导入任务开始前）会恢复遗留导入；维护服务还可删除词典后的孤立音频，并使用 SQLite backup API 生成包含
`dictionary.db`、全部音频分片和 `integrity.json` 的一致备份。Windows standalone 包可用：

```powershell
pyside6-deploy -c pysidedeploy.spec
powershell -ExecutionPolicy Bypass -File scripts/verify_distribution.ps1
python scripts/benchmark_catalog.py --entries 1000000 --audio-resources 200000
```


MDict Files
===========
MDict stores the dictionary definitions, i.e. (key word, explanation) in MDX file and
the dictionary reference data, e.g. images, pronunciations, stylesheets in MDD file.
Although holding different contents, these two file formats share the same structure.

MDX File Format
===============
<img src="https://rawgit.com/csarron/mdict-analysis/master/MDX.svg">


MDD File Format
===============
<img src="https://rawgit.com/csarron/mdict-analysis/master/MDD.svg">


Example Programs
================

`mdict_utils.readmdict`
----------------------
`mdict_utils.readmdict` is an example implementation in Python. It can read and extract MDX/MDD files.

.. note:: python-lzo is required to read mdx files created with engine 1.2.
   Get Windows version from http://www.lfd.uci.edu/~gohlke/pythonlibs/#python-lzo

It can be used as a command line tool. Suppose one has oald8.mdx and oald8.mdd::

    $ python -m mdict_utils.readmdict -x oald8.mdx

This will creates *oald8.txt* dictionary file and creates a folder *data* for images, pronunciation audio files.

On Windows, one can also double click it and select the file in the popup dialog.

Or as a module::

    In [1]: from mdict_utils.readmdict import MDX, MDD

Read MDX file and print the first entry::

    In [2]: mdx = MDX('oald8.mdx')

    In [3]: items = mdx.items()

    In [4]: items.next()
    Out[4]:
    ('A',
     '<span style=\'display:block;color:black;\'>.........')
``mdx`` is an object having all info from a MDX file. ``items`` is an iterator producing 2-item tuples.
Of each tuple, the first element is the entry text and the second is the explanation. Both are UTF-8 encoded strings.

Read MDD file and print the first entry::

    In [5]: mdd = MDD('oald8.mdd')

    In [6]: items = mdd.items()

    In [7]: items = mdd.next()
    Out[7]: 
    (u'\\pic\\accordion_concertina.jpg',
    '\xff\xd8\xff\xe0\x00\x10JFIF...........')

``mdd`` is an object having all info from a MDD file. ``items`` is an iterator producing 2-item tuples. 
Of each tuple, the first element is the file name and the second element is the corresponding file content.
The file name is encoded in UTF-8. The file content is a plain bytes array.

Acknowledge
===========
The file format gets fully disclosed by https://github.com/zhansliu/writemdict.
The encryption part is taken into this project.
