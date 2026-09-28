# Third-party notices

## WaterThreeJS

`src/renderer/world/water-surface.js` retains the water/air Fresnel boundary
from the previous Nirai surface, originally derived from
[WaterThreeJS](https://github.com/achrefelouafi/WaterThreeJS). Its MIT notice is retained below.
Nirai v2 does not bundle WaterThreeJS as a runtime dependency.

MIT License

Copyright (c) 2026 mohamedachrefelouafi

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

## Other local assets and packages

- `resources/world/ground-sand-005-*.webp`: the existing Nirai v1 2K sand textures.
- `AmbientBubbleField.js`: Nirai v1's environment-only ambient bubble renderer.
- Three.js and @pixiv/three-vrm are version-pinned npm dependencies. Their license
  files remain in their packages; the renderer build retains bundled legal comments.
- Avatar files remain user-selected local files. No third-party character model is
  distributed in this repository.

## caustic-volume

[caustic-volume](https://github.com/ScottieFox/caustic-volume), especially
`lite/index.html`, supplies the random-sea approach adapted in `waves.js`: 24
directions, independent phase/amplitude cycles and wandering crests. Nirai uses
periodic directions, calmer waves and a shared height/slope texture. `caustics.js`
also follows its refracted-grid / projected-area method for focused sunlight.
The project is not an additional runtime dependency. The adapted portions are
covered by this notice:

MIT License

Copyright (c) 2026 Scottie

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
