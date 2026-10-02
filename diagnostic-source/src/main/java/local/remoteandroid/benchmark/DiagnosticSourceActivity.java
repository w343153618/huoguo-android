package local.remoteandroid.benchmark;

import android.app.Activity;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Typeface;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.Choreographer;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.window.OnBackInvokedDispatcher;

import java.util.Locale;
import java.util.UUID;

/** A guest-side synthetic source. Normal diagnostics retain the fixed 30 FPS scene. */
public final class DiagnosticSourceActivity extends Activity {
    private static final String TAG = "DiagnosticSource";
    private SceneView scene;
    private volatile boolean audioProbeRunning;

    @Override
    public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().setDecorFitsSystemWindows(false);
        WindowManager.LayoutParams attributes = getWindow().getAttributes();
        attributes.layoutInDisplayCutoutMode =
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
        getWindow().setAttributes(attributes);
        int requestedFps = getIntent().getIntExtra("source_fps", 30);
        scene = new SceneView(this, validatedRunId(getIntent().getStringExtra("run_id")),
                requestedFps == 120 ? 120 : requestedFps == 60 ? 60 : 30,
                "nearest".equals(getIntent().getStringExtra("source_clock")));
        scene.setOnApplyWindowInsetsListener((view, insets) -> {
            scene.safeInsets = insets.getInsetsIgnoringVisibility(WindowInsets.Type.displayCutout());
            scene.invalidate();
            return insets;
        });
        setContentView(scene);
        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::finish);
        }
        hideSystemBars();
        scene.requestApplyInsets();
    }

    @Override
    protected void onResume() {
        super.onResume();
        scene.start();
        if (getIntent().getBooleanExtra("audio_probe", false)) startAudioProbe();
    }

    @Override
    protected void onPause() {
        audioProbeRunning = false;
        scene.stop();
        super.onPause();
    }

    private void startAudioProbe() {
        audioProbeRunning = true;
        // Active, silent media supplies real AudioRecord/AAC timestamps without
        // recording private content or playing an audible test tone on the Mac.
        new Thread(() -> {
            AudioTrack track = null;
            try {
                track = new AudioTrack.Builder()
                        .setAudioAttributes(new AudioAttributes.Builder()
                                .setUsage(AudioAttributes.USAGE_MEDIA)
                                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
                        .setAudioFormat(new AudioFormat.Builder().setSampleRate(48000)
                                .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
                                .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                        .setTransferMode(AudioTrack.MODE_STREAM).setBufferSizeInBytes(19200).build();
                byte[] silence = new byte[4096];
                track.play();
                while (audioProbeRunning) {
                    int count = track.write(silence, 0, silence.length, AudioTrack.WRITE_NON_BLOCKING);
                    if (count < 0) throw new IllegalStateException("Silent audio probe write failed");
                    if (count == 0) Thread.sleep(5);
                }
            } catch (Exception exception) {
                Log.w(TAG, "synthetic_audio_probe_failed type=" + exception.getClass().getSimpleName());
            } finally {
                if (track != null) { track.stop(); track.release(); }
            }
        }, "synthetic-audio").start();
    }

    @Override
    @android.annotation.SuppressLint("GestureBackNavigation")
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        // Fallback for API 30-32; newer releases use the native callback above.
        finish();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemBars();
    }

    private void hideSystemBars() {
        WindowInsetsController controller = getWindow().getInsetsController();
        if (controller != null) {
            controller.setSystemBarsBehavior(
                    WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            controller.hide(WindowInsets.Type.systemBars());
        }
    }

    private static String validatedRunId(String candidate) {
        if (candidate == null || !candidate.matches(
                "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")) {
            return "unspecified";
        }
        return UUID.fromString(candidate).toString();
    }

    private static final class SceneView extends View implements Choreographer.FrameCallback {
        static final int CYCLE_SECONDS = 20;
        static final float WIDTH = 540f;
        static final float HEIGHT = 1200f;
        static final float LIST_TOP = 166f;
        static final float LIST_BOTTOM = 1098f;
        static final float CARD_PITCH = 252f;
        static final int BACKGROUND = Color.rgb(16, 25, 43);
        static final int[] COLORS = {
                0xff64b5f6, 0xfff6ad7b, 0xff9bdebb, 0xffb5a1ee,
                0xffefd17b, 0xff79ced5, 0xfff29bad, 0xffa1c5ef
        };
        static final String[] TITLES = {
                "COBALT / 01", "AMBER / 02", "MINT / 03", "IRIS / 04",
                "GOLD / 05", "TIDAL / 06", "CORAL / 07", "SKY / 08"
        };
        final int targetFps;
        final int cycleFrames;
        final String[] frameLabels;
        final boolean nearestTick;
        final Choreographer choreographer = Choreographer.getInstance();
        final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        final Path path = new Path();
        final String runId;
        Insets safeInsets = Insets.NONE;
        boolean running;
        boolean hardwareCanvas;
        long epochNanos;
        long sourceFrame;
        long lastScheduledFrame;
        long lastDrawnFrame;
        long uniqueDrawnFrames;
        long skippedSourceTicks;
        long callbackFrames;
        long repeatedTargetTicks;

        SceneView(Context context, String runId, int targetFps, boolean nearestTick) {
            super(context);
            this.runId = runId;
            this.targetFps = targetFps;
            this.nearestTick = nearestTick && targetFps >= 60;
            cycleFrames = targetFps * CYCLE_SECONDS;
            frameLabels = frameLabels(cycleFrames);
            setContentDescription("Deterministic synthetic scrolling scene for stream diagnostics");
            setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        }

        void start() {
            if (running) return;
            running = true;
            epochNanos = 0;
            sourceFrame = 0;
            lastScheduledFrame = -1;
            lastDrawnFrame = -1;
            uniqueDrawnFrames = 0;
            skippedSourceTicks = 0;
            callbackFrames = repeatedTargetTicks = 0;
            hardwareCanvas = false;
            invalidate();
            choreographer.postFrameCallback(this);
            Log.i(TAG, "synthetic_start run_id=" + runId + " target_fps=" + targetFps
                    + " cycle_seconds=" + CYCLE_SECONDS + " source_clock=" + (nearestTick ? "nearest" : "floor"));
        }

        void stop() {
            if (!running) return;
            running = false;
            choreographer.removeFrameCallback(this);
            Log.i(TAG, "synthetic_stop run_id=" + runId + " source_frame=" + sourceFrame
                    + " unique_drawn_frames=" + uniqueDrawnFrames
                    + " skipped_source_ticks=" + skippedSourceTicks
                    + " callback_frames=" + callbackFrames + " repeated_target_ticks=" + repeatedTargetTicks
                    + " hardware_canvas=" + hardwareCanvas);
        }

        @Override
        public void doFrame(long frameTimeNanos) {
            if (!running) return;
            if (epochNanos == 0) epochNanos = frameTimeNanos;
            callbackFrames++;
            long frame = ((frameTimeNanos - epochNanos) * targetFps + (nearestTick ? 500_000_000L : 0)) / 1_000_000_000L;
            if (frame > lastScheduledFrame) {
                if (lastScheduledFrame >= 0) {
                    skippedSourceTicks += Math.max(0, frame - lastScheduledFrame - 1);
                }
                sourceFrame = frame;
                lastScheduledFrame = frame;
                invalidate();
            } else {
                repeatedTargetTicks++;
            }
            choreographer.postFrameCallback(this);
        }

        @Override
        public boolean onTouchEvent(MotionEvent event) {
            StringBuilder pointers = new StringBuilder();
            for (int index = 0; index < event.getPointerCount(); index++) {
                if (index > 0) pointers.append(',');
                pointers.append(event.getPointerId(index));
            }
            // Dedicated diagnostic scene only; no unrelated app input is recorded.
            Log.i(TAG, "synthetic_touch run_id=" + runId
                    + " action=" + event.getActionMasked()
                    + " count=" + event.getPointerCount()
                    + " ids=" + pointers + " source=" + event.getSource()
                    + " x=" + event.getX() + " y=" + event.getY());
            return true;
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            hardwareCanvas = canvas.isHardwareAccelerated();
            canvas.drawColor(BACKGROUND);
            float availableWidth = getWidth() - safeInsets.left - safeInsets.right;
            float availableHeight = getHeight() - safeInsets.top - safeInsets.bottom;
            if (availableWidth <= 0 || availableHeight <= 0) return;
            if (running && sourceFrame != lastDrawnFrame) {
                uniqueDrawnFrames++;
                lastDrawnFrame = sourceFrame;
            }
            float scale = Math.min(availableWidth / WIDTH, availableHeight / HEIGHT);
            canvas.save();
            canvas.translate(safeInsets.left + (availableWidth - WIDTH * scale) / 2f,
                    safeInsets.top + (availableHeight - HEIGHT * scale) / 2f);
            canvas.scale(scale, scale);
            int cycleFrame = (int) (sourceFrame % cycleFrames);
            float phase = cycleFrame * (float) (2 * Math.PI / cycleFrames);
            drawHeader(canvas, cycleFrame);
            drawList(canvas, cycleFrame, phase);
            drawFooter(canvas, cycleFrame);
            canvas.restore();
        }

        private void drawHeader(Canvas canvas, int cycleFrame) {
            text(canvas, "SYNTHETIC STREAM CHECK", 24, 62, 26, Color.WHITE, true);
            text(canvas, "Fixed scene  /  " + targetFps + " FPS source target", 24, 98, 19, 0xffbbcbde, false);
            text(canvas, frameLabels[cycleFrame], 24, 136, 18, 0xff8be0cb, true);
            fill(0xff263a52);
            canvas.drawRect(24, 151, 516, 154, paint);
        }

        private void drawList(Canvas canvas, int cycleFrame, float phase) {
            float scroll = cycleFrame * (COLORS.length * CARD_PITCH / cycleFrames);
            int firstCard = (int) (scroll / CARD_PITCH);
            float offset = scroll % CARD_PITCH;
            canvas.save();
            canvas.clipRect(0, LIST_TOP, WIDTH, LIST_BOTTOM);
            for (int row = -1; row < 5; row++) {
                int index = Math.floorMod(firstCard + row, COLORS.length);
                drawCard(canvas, index, LIST_TOP + row * CARD_PITCH - offset, phase);
            }
            canvas.restore();
        }

        private void drawCard(Canvas canvas, int index, float y, float phase) {
            int accent = COLORS[index];
            fill(0xff21314a);
            canvas.drawRoundRect(24, y, 516, y + 232, 18, 18, paint);
            fill(accent);
            canvas.drawRoundRect(24, y, 33, y + 232, 4, 4, paint);
            text(canvas, TITLES[index], 48, y + 37, 22, accent, true);
            text(canvas, "SCROLL  +  TEXT  +  VECTOR MOTION", 48, y + 65, 13, 0xffc7d4e6, false);
            fill(0xff15243a);
            canvas.drawRoundRect(48, y + 83, 330, y + 176, 8, 8, paint);
            fill(0xff425774);
            for (int line = 1; line < 4; line++) {
                canvas.drawRect(56, y + 83 + line * 22, 322, y + 84 + line * 22, paint);
            }
            path.reset();
            for (int point = 0; point <= 28; point++) {
                float x = 56 + point * 9.5f;
                float curve = (float) Math.sin(point * 0.46 + phase * 4 + index * 0.7);
                float py = y + 130 + curve * 27;
                if (point == 0) path.moveTo(x, py); else path.lineTo(x, py);
            }
            paint.setColor(accent);
            paint.setStrokeWidth(3);
            paint.setStyle(Paint.Style.STROKE);
            canvas.drawPath(path, paint);
            paint.setStyle(Paint.Style.FILL);
            float orbX = 431 + 27 * (float) Math.sin(phase * 4 + index);
            float orbY = y + 126 + 19 * (float) Math.cos(phase * 2 + index);
            fill(accent);
            canvas.drawCircle(orbX, orbY, 21, paint);
            canvas.save();
            canvas.translate(431, y + 128);
            canvas.rotate(phase * 180 / (float) Math.PI + index * 45);
            paint.setColor(0xffd5e2f3);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(3);
            canvas.drawRect(-52, -43, 52, 43, paint);
            paint.setStyle(Paint.Style.FILL);
            canvas.restore();
            for (int bar = 0; bar < 12; bar++) {
                float height = 5 + 17 * (1 + (float) Math.sin(phase * 5 + bar * 0.8 + index)) / 2;
                fill(accent);
                canvas.drawRect(48 + bar * 14, y + 212 - height, 57 + bar * 14, y + 212, paint);
            }
            text(canvas, "0123456789  AaBbCc", 242, y + 210, 18, 0xffd5e2f3, false);
        }

        private void drawFooter(Canvas canvas, int cycleFrame) {
            fill(BACKGROUND);
            canvas.drawRect(0, LIST_BOTTOM, WIDTH, HEIGHT, paint);
            fill(0xff314864);
            canvas.drawRoundRect(24, 1116, 516, 1124, 4, 4, paint);
            fill(0xff8be0cb);
            float x = 24 + cycleFrame * (444f / cycleFrames);
            canvas.drawRoundRect(x, 1116, x + 48, 1124, 4, 4, paint);
            text(canvas, "20 s loop  /  synthetic content only", 24, 1157, 18, 0xffbbcbde, false);
            text(canvas, "BACK exits", 24, 1185, 15, 0xff8ba3bf, false);
        }

        private void fill(int color) {
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(color);
        }

        private void text(Canvas canvas, String value, float x, float y, float size,
                int color, boolean bold) {
            fill(color);
            paint.setTextSize(size);
            paint.setTypeface(bold ? Typeface.DEFAULT_BOLD : Typeface.DEFAULT);
            canvas.drawText(value, x, y, paint);
        }

        private static String[] frameLabels(int cycleFrames) {
            String[] labels = new String[cycleFrames];
            for (int i = 0; i < labels.length; i++) {
                labels[i] = String.format(Locale.ROOT, "FRAME %03d / %d", i, cycleFrames);
            }
            return labels;
        }
    }
}
