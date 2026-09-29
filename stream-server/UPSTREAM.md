Derived from Genymobile/scrcpy v4.1, commit 2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0.
Upstream: https://github.com/Genymobile/scrcpy/tree/v4.1/server
License: Apache 2.0; see LICENSE.

Local extension: control message 240 contains a big-endian int video bitrate.
The encoder applies MediaCodec.PARAMETER_KEY_VIDEO_BITRATE on its encoding thread,
without restarting the codec or session. Device message 240 acknowledges the
accepted bitrate, or 0 for a rejected/unsupported change. Requests are constrained
to 500,000 bits/s through the initial session's selected target bitrate.
The extension is used only with this server; standard CBR/VBR sessions retain
the unmodified upstream server and wire protocol.

The extension declares Android API 24 minimum for IntConsumer callbacks. Deployed Android 17/API37 meets this requirement. The video-control implementation is the same code verified in the native and LAN/WAN tests.
