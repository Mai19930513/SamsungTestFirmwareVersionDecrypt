# Third-party notices

The FUS request structure, initialization logic and firmware key derivation in
`samsung_firmware/download.py` were adapted from protocol implementations in
[Bifrost / SamloaderKotlin](https://github.com/zacharee/SamloaderKotlin), commit
`ed73a034d3b27d84c68b2c21622a9de6618455de` (Request.kt, FusClient.kt, IFusClient.kt,
CryptUtils.kt). No third-party CRC/MD5 implementation is copied: this project
uses Python hashlib/zlib and cryptography. Bifrost itself builds on samloader.

MIT License

Copyright (c) 2021 Zachary Wander

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
