# Desktop dependency notices

PixelSystem Buddy uses the following projects. Their upstream licenses and
copyright notices continue to apply; this file does not replace them.

| Project | License and upstream source |
| --- | --- |
| pypdfium2 | Apache-2.0 / BSD-3-Clause; [upstream](https://github.com/pypdfium2-team/pypdfium2) |
| PDFium and bundled libraries | BSD and additional dependency licenses included in the pypdfium2 wheel's `dist-info/licenses` directory |
| Pillow | HPND and bundled-library notices; [upstream](https://github.com/python-pillow/Pillow) |
| Trafilatura | Apache-2.0; [upstream](https://github.com/adbar/trafilatura) |
| RapidFuzz | MIT; [upstream](https://github.com/rapidfuzz/RapidFuzz) |
| Tesseract (optional external executable) | Apache-2.0; [upstream](https://github.com/tesseract-ocr/tesseract) |
| py-spy (optional developer tool) | MIT; [upstream](https://github.com/benfred/py-spy) |
| Sentence Transformers (optional) | Apache-2.0; [upstream](https://github.com/huggingface/sentence-transformers) |
| all-MiniLM-L6-v2 (optional local model) | Apache-2.0; [model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) |
| PyTorch (optional) | BSD-3-Clause and bundled-library notices; [upstream](https://github.com/pytorch/pytorch) |

The Windows build collects pypdfium2 metadata, including the PDFium and library
license files supplied by the installed platform wheel. Retain that metadata
when repackaging the executable. The original notices are bundled inside the
PyInstaller archive; `pyi-archive_viewer` can inspect that archive. Tesseract,
language data, developer tools, and semantic model weights are not included in
the standard desktop build. Preserve their original licenses if distributing
them separately. The optional semantic build includes its installed library
metadata; model downloads retain the upstream model card and available license
files locally.

Existing dependencies retain their licenses as well: Qt/PySide6 (LGPLv3/GPLv3
or commercial terms), psutil (BSD-3-Clause), pypdf (BSD-3-Clause), and PyInstaller
(GPL with its application-distribution exception). Refer to the installed
distributions and upstream documentation for complete terms and notices.
