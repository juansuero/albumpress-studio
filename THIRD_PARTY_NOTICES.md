# Third-party notices

AlbumPress Studio is distributed under the MIT License. Its dependencies remain subject to their own licenses and terms.

In particular, the video preview and rendering features use [Remotion](https://www.remotion.dev/), which uses the Remotion License rather than MIT. Remotion's current terms distinguish individuals and small teams from larger companies and automated video products. Review the [official Remotion licensing information](https://www.remotion.dev/) before using those features in a commercial or larger-team context.

The frontend lockfile records declared package licenses. To inspect the installed dependency tree and current security advisories, run:

```powershell
npm ls --prefix frontend
npm audit --prefix frontend
```

Python and system dependencies, including `audio-separator`, FFmpeg, and model files, also retain their respective licenses. Model availability in the application catalogue does not grant redistribution or commercial-use rights.
