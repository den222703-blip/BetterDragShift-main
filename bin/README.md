# bin/ - helper programs (not stored in git)

DragShift's video tools and AI upscaler call two outside programs. Put them here **before you build
the .exe** and the build packs them in. When you only run from source you can instead install them
system-wide (ffmpeg via winget) or into `%LOCALAPPDATA%\DragShift\tools\`.

```
bin\
  ffmpeg.exe              video tools          (winget install Gyan.FFmpeg, or the gyan.dev "essentials" zip)
  ffprobe.exe             video tools          (same download)
  realesrgan\             AI upscaler          (realesrgan-ncnn-vulkan-20220424-windows.zip, Real-ESRGAN v0.2.5.0)
    realesrgan-ncnn-vulkan.exe
    *.dll                 keep every .dll that came in the zip
    models\
      realesrgan-x4plus.param       realesrgan-x4plus.bin           (Photo)
      realesrgan-x4plus-anime.param realesrgan-x4plus-anime.bin     (Illustration / anime)
```

Only this file is committed; the programs themselves are ignored by `.gitignore`.
Full step-by-step instructions: `docs/SETUP-RU.md`.
