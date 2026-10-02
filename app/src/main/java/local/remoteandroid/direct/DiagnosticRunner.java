package local.remoteandroid.direct;

import android.app.AlertDialog;
import android.content.Context;
import android.media.AudioManager;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.BatteryManager;
import android.os.Build;
import android.os.PowerManager;
import android.view.Gravity;
import android.view.View;
import android.widget.*;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Foreground synthetic stream tests. Codec callbacks do not measure physical display presentation. */
final class DiagnosticRunner {
    static final String LAST_REPORT = "diagnostic-last.json";
    final MainActivity activity;
    final String credential, account, host, networkLabel, runId = UUID.randomUUID().toString();
    final DiagnosticHttp http;
    final ExecutorService worker = Executors.newSingleThreadExecutor();
    final List<Profile> profiles = new ArrayList<>();
    final JSONArray stages = new JSONArray();
    final String mode;
    final int seconds;
    volatile DiagnosticMetrics measured;
    volatile boolean finished, closed;
    volatile long firstRenderNs;
    int index = -1;
    long stageToken, phaseNs, endpointNs, initialNetwork;
    int thermalStart, batteryStart;
    double batteryTempStart;
    long lastCpuMs, lastCpuNs;
    double javaHeapPeak, nativeHeapPeak;
    final List<Double> cpuSamples = new ArrayList<>();
    boolean networkChanged;
    String initialTransport;
    JSONArray samples;
    TextView progress, resultStatus;
    String reportId = "", uploadError = "";
    String cleanupWarning = "";

    static final class Profile {
        final String label, mode;
        final int size, bitrate;
        Profile(String label, int size, int bitrate, String mode) {
            this.label = label; this.size = size; this.bitrate = bitrate; this.mode = mode;
        }
    }

    DiagnosticRunner(MainActivity activity, String credential, String account, String networkLabel, boolean full) {
        this.activity = activity; this.credential = credential; this.account = account; this.host = activity.host;
        this.networkLabel = networkLabel; this.mode = full ? "full" : "quick";
        seconds = full ? 30 : 20;
        http = new DiagnosticHttp(activity, credential, host);
        profiles.add(new Profile("540P · 2.5 Mbps · VBR", 960, 2500000, "VBR"));
        profiles.add(new Profile("540P · 4 Mbps · VBR", 960, 4000000, "VBR"));
        profiles.add(new Profile("540P · 8 Mbps · VBR", 960, 8000000, "VBR"));
        profiles.add(new Profile("720P · 4 Mbps · VBR", 1280, 4000000, "VBR"));
        profiles.add(new Profile("540P · 4 Mbps · 自适应 VBR", 960, 4000000, "ADAPTIVE_VBR"));
        if (full) for (int i = 4; i >= 0; i--) profiles.add(profiles.get(i));
        else profiles.add(profiles.get(0));
    }

    static void choose(MainActivity activity) {
        LinearLayout box = new LinearLayout(activity); box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(32, 16, 32, 8);
        TextView hint = new TextView(activity);
        hint.setText("保持平时使用的 Wi-Fi。请先停止 RustDesk 屏幕共享，避免第二路串流干扰。\n"
                + "自动播放统一滚动画面，测试 2.5 / 4 / 8 Mbps。完成后通过当前服务器的加密连接上传帧率、卡顿、解码器和网络状态；不上传屏幕、照片、密码、Wi-Fi 名称或手机唯一标识。\n"
                + "测试临时使用参数，结束后保留原连接设置。快速约 3–4 分钟，详细约 7–8 分钟；网络较慢时可能更久。");
        box.addView(hint);
        Spinner label = new Spinner(activity);
        String[] labels = {"东北移动宽带 Wi-Fi", "其他 Wi-Fi", "手机流量", "其他 / 不确定"};
        label.setAdapter(new ArrayAdapter<>(activity, android.R.layout.simple_spinner_dropdown_item, labels));
        box.addView(label);
        new AlertDialog.Builder(activity).setTitle("一键检测 · 自动提交报告").setView(box)
                .setPositiveButton("快速检测", (d, w) -> begin(activity, labels[label.getSelectedItemPosition()], false))
                .setNeutralButton("详细检测", (d, w) -> begin(activity, labels[label.getSelectedItemPosition()], true))
                .setNegativeButton("取消", null).show();
    }

    static void begin(MainActivity activity, String label, boolean full) {
        try {
            activity.host = Endpoint.destination(activity.address.getText().toString());
            String name = activity.user.getText().toString(), secret = activity.password.getText().toString();
            if (name.isEmpty() || secret.isEmpty()) throw new IllegalArgumentException("请填写账号和密码，或先保存密码");
            String auth = "Basic " + android.util.Base64.encodeToString((name + ":" + secret).getBytes(StandardCharsets.UTF_8), 2);
            activity.getSharedPreferences("connection", 0).edit().putString("host", activity.host).putString("username", name).apply();
            activity.password.setText("");
            DiagnosticRunner runner = new DiagnosticRunner(activity, auth, name, label, full);
            activity.diagnostics = runner; runner.start();
        } catch (Exception e) { activity.status.setText("检测未开始：" + MainActivity.message(e)); }
    }

    void start() {
        synchronized (DiagnosticRunner.class) {
            activity.getSharedPreferences("connection", 0).edit().putString("diagnostic_active_run_id", runId).apply();
        }
        LinearLayout box = new LinearLayout(activity); box.setOrientation(LinearLayout.VERTICAL); box.setPadding(32, 64, 32, 32);
        progress = new TextView(activity); progress.setText("正在登录并准备统一测试画面…"); box.addView(progress);
        Button cancel = new Button(activity); cancel.setText("取消检测"); cancel.setOnClickListener(v -> cancel()); box.addView(cancel);
        activity.setContentView(box);
        worker.execute(() -> {
            try { activity.initTLS(); http.request("GET", "/diagnostics/reports", null);
                activity.runOnUiThread(() -> { if (!finished && !closed) next(); });
            } catch (Exception e) { activity.runOnUiThread(() -> finish("failed", MainActivity.message(e))); }
        });
    }

    void next() {
        if (finished || closed) return;
        measured = null; activity.stop();
        if (++index >= profiles.size()) { finish("completed", ""); return; }
        Profile profile = profiles.get(index);
        firstRenderNs = 0; phaseNs = System.nanoTime(); endpointNs = 0; samples = new JSONArray(); networkChanged = false;
        long token = ++stageToken;
        activity.auth = credential; activity.maxSize = profile.size; activity.bitRate = profile.bitrate;
        activity.bitrateMode = profile.mode; activity.maxFps = 30; activity.bufferMs = 80;
        progress.setText("准备第 " + (index + 1) + "/" + profiles.size() + " 组 · " + profile.label);
        worker.execute(() -> {
            try {
                http.request("POST", "/diagnostics/source", new JSONObject().put("action", "start").put("run_id", runId));
                if (finished || closed || token != stageToken) return;
                String id = activity.session();
                activity.runOnUiThread(() -> {
                    if (finished || closed || token != stageToken) return;
                    activity.show(id); activity.ui.postDelayed(() -> warmup(token), 500);
                });
            } catch (Exception e) { activity.runOnUiThread(() -> failedStage(token, MainActivity.message(e))); }
        });
    }

    void decorate(android.widget.FrameLayout canvas) {
        // Synthetic test is not an interactive remote session: prevent accidental guest changes.
        activity.screen.setOnTouchListener((v, e) -> true);
        progress = new TextView(activity); progress.setTextColor(0xffffffff); progress.setBackgroundColor(0xdd234e4c);
        progress.setTextSize(13); progress.setPadding(16, 8, 16, 8);
        FrameLayout.LayoutParams text = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM);
        text.bottomMargin = CuteUi.dp(activity, 48); canvas.addView(progress, text);
        Button cancel = new Button(activity); cancel.setText("停止检测并保存报告"); CuteUi.style(cancel, CuteUi.PINK, true);
        cancel.setOnClickListener(v -> cancel()); canvas.addView(cancel, new FrameLayout.LayoutParams(-1, CuteUi.dp(activity, 48), Gravity.BOTTOM));
    }

    void warmup(long token) {
        if (!active(token)) return;
        long now = System.nanoTime();
        if (firstRenderNs == 0) {
            progress.setText("第 " + (index + 1) + "/" + profiles.size() + " 组 · 等待手机解码首帧…");
            if (now - phaseNs > 25000000000L) { failedStage(token, "25 秒内没有收到首帧解码回调"); return; }
            activity.ui.postDelayed(() -> warmup(token), 500); return;
        }
        long warmMs = (now - firstRenderNs) / 1000000;
        if (warmMs < 5000) {
            progress.setText("第 " + (index + 1) + "/" + profiles.size() + " 组 · 预热 " + (5 - warmMs / 1000) + " 秒");
            activity.ui.postDelayed(() -> warmup(token), 500); return;
        }
        DiagnosticMetrics metrics = new DiagnosticMetrics(); metrics.start(now, 30, true); measured = metrics;
        thermalStart = thermal(); batteryStart = battery(); initialNetwork = networkId(); initialTransport = transport();
        batteryTempStart = batteryTemperature(); lastCpuMs = android.os.Process.getElapsedCpuTime(); lastCpuNs = now;
        cpuSamples.clear(); javaHeapPeak = 0; nativeHeapPeak = 0;
        endpointNs = now + seconds * 1000000000L;
        activity.ui.postDelayed(() -> sample(token, now), 1000);
    }

    void sample(long token, long startNs) {
        if (!active(token) || measured == null) return;
        long now = System.nanoTime(); DiagnosticMetrics.Snapshot snap = measured.snapshot(Math.min(now, endpointNs));
        try {
            long cpu = android.os.Process.getElapsedCpuTime();
            double cpuPercent = Math.min(6400, Math.max(0, (cpu - lastCpuMs) * 100000000.0 / Math.max(1, now - lastCpuNs)));
            lastCpuMs = cpu; lastCpuNs = now; cpuSamples.add(cpuPercent);
            Runtime runtime = Runtime.getRuntime(); double javaMb = (runtime.totalMemory() - runtime.freeMemory()) / 1048576.0;
            double nativeMb = android.os.Debug.getNativeHeapAllocatedSize() / 1048576.0;
            javaHeapPeak = Math.max(javaHeapPeak, javaMb); nativeHeapPeak = Math.max(nativeHeapPeak, nativeMb);
            JSONObject sample = new JSONObject().put("elapsed_ms", (Math.min(now, endpointNs) - startNs) / 1000000)
                    .put("received_frames", snap.receivedFrames).put("rendered_frames", snap.renderedFrames)
                    .put("codec_callback_frames", snap.renderedFrames)
                    .put("video_bytes", snap.receivedBytes).put("rtt_ms", activity.networkRttMs).put("thermal_status", thermal())
                    .put("app_cpu_percent", cpuPercent).put("java_heap_mb", javaMb).put("native_heap_mb", nativeMb);
            int signal = signalStrength(); if (signal >= -150 && signal < 0) sample.put("signal_strength_dbm", signal);
            samples.put(sample);
        } catch (Exception ignored) {}
        networkChanged |= networkId() != initialNetwork || !transport().equals(initialTransport);
        Profile p = profiles.get(index);
        progress.setText("第 " + (index + 1) + "/" + profiles.size() + " 组 · " + p.label + "\n检测剩余 "
                + Math.max(0, (endpointNs - now) / 1000000000L) + " 秒 · 请保持 App 在前台");
        if (now >= endpointNs) {
            long endpoint = endpointNs;
            activity.ui.postDelayed(() -> completeStage(token, endpoint), 250);
        } else activity.ui.postDelayed(() -> sample(token, startNs), 1000);
    }

    boolean active(long token) { return !finished && !closed && token == stageToken; }
    void received(int bytes, long pts, long ns, boolean config) { DiagnosticMetrics m = measured; if (m != null) m.received(bytes, pts, ns, config); }
    void rendered(long pts, long vendorNs) {
        // Vendor nanoTime can echo the requested future release target. These
        // foreground diagnostics measure Java callback receipt only; do not
        // feed that unverified timestamp into cadence or pipeline estimates.
        long receiptNs = System.nanoTime();
        if (firstRenderNs == 0) firstRenderNs = receiptNs;
        DiagnosticMetrics m = measured; if (m != null) m.rendered(pts, receiptNs);
    }
    void discarded() { DiagnosticMetrics m = measured; if (m != null) m.discarded(); }
    void rtt(long ms) { DiagnosticMetrics m = measured; if (m != null) m.rtt(ms); }

    JSONObject baseStage() throws Exception {
        Profile p = profiles.get(index);
        return new JSONObject().put("label", p.label).put("max_size", p.size).put("bitrate", p.bitrate)
                .put("max_fps", 30).put("mode", p.mode).put("buffer_ms", 80).put("dimensions", activity.width + "x" + activity.height)
                .put("decoder_name", activity.videoDecoderName).put("hardware_decoder", activity.hardwareVideo)
                .put("codec_timing_basis", "java_codec_callback_receipt")
                .put("vendor_timestamp_status", "not_used_for_diagnostic_timing")
                .put("actual_display_fps_measured", false).put("actual_audio_video_skew_measured", false)
                .put("accepted_bitrate", activity.acceptedBitrate).put("adaptive_rejected", activity.adaptiveRejected)
                .put("thermal_start", thermalStart).put("thermal_end", thermal()).put("battery_start", batteryStart).put("battery_end", battery())
                .put("transport", transport()).put("vpn_present", vpn()).put("network_changed", networkChanged)
                .put("source_startup_ms", firstRenderNs == 0 ? 0 : Math.max(0, (firstRenderNs - phaseNs) / 1000000)).put("samples", samples == null ? new JSONArray() : samples);
    }

    void completeStage(long token, long endpoint) {
        if (!active(token) || measured == null) return;
        try {
            DiagnosticMetrics.Result r = measured.finish(endpoint); measured = null;
            JSONObject stage = baseStage().put("elapsed_ms", r.elapsedMs).put("received_frames", r.receivedFrames)
                    .put("rendered_frames", r.renderedFrames).put("codec_callback_frames", r.renderedFrames).put("video_bytes", r.receivedBytes)
                    .put("received_fps", r.receiveFps).put("rendered_fps", r.renderFps).put("codec_callback_fps", r.renderFps).put("receive_mbps", r.receiveMbps)
                    .put("render_gap_count", r.renderGapCount).put("max_render_gap_ms", r.maxRenderGapMs).put("discarded_frames", r.discardedFrames)
                    .put("valid", r.valid && !networkChanged).put("invalid_reason", networkChanged ? "检测中网络切换" : r.invalidReason);
            finite(stage, "last_receive_ago_ms", r.lastReceiveAgoMs); finite(stage, "last_render_ago_ms", r.lastRenderAgoMs);
            finite(stage, "receive_interval_jitter_ms", r.receiveIntervalJitterMs);
            finite(stage, "render_interval_jitter_ms", r.renderIntervalJitterMs);
            finite(stage, "source_interval_jitter_ms", r.sourceIntervalJitterMs);
            finite(stage, "rtt_p50_ms", r.rttP50Ms); finite(stage, "rtt_p95_ms", r.rttP95Ms);
            finite(stage, "client_pipeline_p50_ms", r.clientPipelineP50Ms); finite(stage, "client_pipeline_p95_ms", r.clientPipelineP95Ms);
            finite(stage, "app_cpu_p50_percent", percentile(cpuSamples, 0.5)); finite(stage, "app_cpu_p95_percent", percentile(cpuSamples, 0.95));
            finite(stage, "app_heap_peak_mb", javaHeapPeak); finite(stage, "native_heap_peak_mb", nativeHeapPeak);
            finite(stage, "battery_temp_start_c", batteryTempStart); finite(stage, "battery_temp_end_c", batteryTemperature());
            stages.put(stage); next();
        } catch (Exception e) { failedStage(token, "这一组数据无法保存"); }
    }

    static void finite(JSONObject object, String key, double value) throws Exception {
        if (Double.isFinite(value)) object.put(key, value);
    }

    void failedStage(long token, String reason) {
        if (!active(token)) return;
        measured = null;
        String safeReason = reason.replaceAll("[\\p{Cntrl}]", " ");
        try { stages.put(baseStage().put("valid", false).put("error", safeReason.substring(0, Math.min(160, safeReason.length())))); }
        catch (Exception ignored) {}
        next();
    }

    void cancel() { if (!finished && !closed) finish("cancelled", "用户停止检测"); }
    void streamFailed(Exception error) { failedStage(stageToken, MainActivity.message(error)); }

    void finish(String status, String reason) {
        if (finished || closed) return; finished = true; stageToken++; measured = null; activity.stop();
        progress.setText("正在保存报告并结束测试画面…");
        worker.execute(() -> {
            try { http.request("POST", "/diagnostics/source", new JSONObject().put("action", "stop").put("run_id", runId)); }
            catch (Exception e) { cleanupWarning = "测试画面停止请求未确认；服务器有超时保护，请联系维护者确认清理结果。"; }
            JSONObject report;
            try {
                AudioManager audio = (AudioManager) activity.getSystemService(Context.AUDIO_SERVICE);
                JSONObject device = new JSONObject().put("manufacturer", Build.MANUFACTURER).put("model", Build.MODEL)
                        .put("android", Build.VERSION.RELEASE).put("sdk", Build.VERSION.SDK_INT)
                        .put("media_volume", (double) audio.getStreamVolume(AudioManager.STREAM_MUSIC) / Math.max(1, audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)))
                        .put("audio_gain", activity.audioGain);
                android.app.ActivityManager.MemoryInfo memory = new android.app.ActivityManager.MemoryInfo();
                ((android.app.ActivityManager) activity.getSystemService(Context.ACTIVITY_SERVICE)).getMemoryInfo(memory);
                device.put("total_memory_mb", memory.totalMem / 1048576.0);
                device.put("avc_hardware_advertised", DeviceCodecs.hardware("video/avc", 540, 960));
                device.put("hevc_hardware_advertised", DeviceCodecs.hardware("video/hevc", 540, 960));
                report = new JSONObject().put("schema_version", 1).put("client_report_id", runId).put("created_at", Instant.now().toString())
                        .put("app_version", BuildConfig.VERSION_NAME).put("device", device)
                        .put("network", new JSONObject().put("transport", transport()).put("label", networkLabel).put("vpn_present", vpn()))
                        .put("mode", mode).put("status", status).put("stages", stages)
                        .put("cleanup_ok", cleanupWarning.isEmpty()).put("cleanup_error", cleanupWarning);
                save(report); upload(report);
            } catch (Exception e) {
                activity.runOnUiThread(() -> { if (!closed) { leave(); activity.status.setText("报告保存失败：" + MainActivity.message(e)); } }); return;
            }
            JSONObject result = report;
            activity.runOnUiThread(() -> { if (!closed) showResult(result, reason); });
        });
    }

    void save(JSONObject report) throws Exception {
        synchronized (DiagnosticRunner.class) {
            android.content.SharedPreferences saved = activity.getSharedPreferences("connection", 0);
            if (!runId.equals(saved.getString("diagnostic_active_run_id", ""))) return;
            try (FileOutputStream out = activity.openFileOutput(LAST_REPORT, Context.MODE_PRIVATE)) { out.write(report.toString().getBytes(StandardCharsets.UTF_8)); }
            saved.edit().remove("diagnostic_uploaded_id").putString("diagnostic_account", account)
                    .putString("diagnostic_report_host", host).putString("diagnostic_client_report_id", runId).apply();
        }
    }
    void upload(JSONObject report) {
        try {
            JSONObject receipt = http.request("POST", "/diagnostics/reports", report); reportId = receipt.getString("report_id"); uploadError = "";
            synchronized (DiagnosticRunner.class) {
                android.content.SharedPreferences saved = activity.getSharedPreferences("connection", 0);
                if (report.optString("client_report_id").equals(saved.getString("diagnostic_client_report_id", ""))
                        && account.equals(saved.getString("diagnostic_account", "")) && host.equals(saved.getString("diagnostic_report_host", "")))
                    saved.edit().putString("diagnostic_uploaded_id", reportId).apply();
            }
        } catch (Exception e) { uploadError = MainActivity.message(e); }
    }

    void showResult(JSONObject report, String reason) {
        LinearLayout box = new LinearLayout(activity); box.setOrientation(LinearLayout.VERTICAL); box.setPadding(28, 48, 28, 24);
        TextView title = new TextView(activity); title.setText("检测报告 · " + report.optJSONObject("device").optString("model")); title.setTextSize(22); box.addView(title);
        resultStatus = new TextView(activity); updateReceipt(); box.addView(resultStatus);
        TextView info = new TextView(activity); StringBuilder lines = new StringBuilder();
        if (!reason.isEmpty()) lines.append(reason).append('\n');
        if (!cleanupWarning.isEmpty()) lines.append(cleanupWarning).append('\n');
        JSONArray all = report.optJSONArray("stages");
        if (all != null) for (int i = 0; i < all.length(); i++) {
            JSONObject stage = all.optJSONObject(i); if (stage == null) continue;
            lines.append(stage.optString("label")).append('\n');
            if (!stage.optBoolean("valid")) lines.append("未取得可用样本：").append(stage.optString("error", stage.optString("invalid_reason", "未完成")));
            else lines.append(String.format(Locale.ROOT, "解码回调 %.1f FPS · 接收 %.2f Mbps · 回调停顿 %d 次 · 最长 %.0f ms", stage.optDouble("codec_callback_fps", stage.optDouble("rendered_fps")), stage.optDouble("receive_mbps"), stage.optInt("render_gap_count"), stage.optDouble("max_render_gap_ms")));
            lines.append("\n\n");
        }
        lines.append("解码回调帧率不是屏幕实际显示帧率，回调停顿也不是肉眼卡顿的直接测量；网络往返不是完整操作延时。本次使用统一测试画面筛选参数，真实视频流畅度与音画同步仍需实测。\n");
        info.setText(lines.toString()); box.addView(info);
        Button retry = button("重新提交报告", () -> {
            retryUpload(report);
        }); box.addView(retry);
        JSONObject recommended = recommendation(all);
        TextView note = new TextView(activity); note.setText(recommended == null ? "没有足够稳定、可比较的数据。保留原设置，报告可交给维护者分析。"
                : "本次候选推荐：" + recommended.optString("label") + "。短测试仅用于筛选，仍需日常使用验证。"); box.addView(note);
        if (recommended != null) box.addView(button("应用本次推荐参数", () -> {
            activity.getSharedPreferences("connection", 0).edit().putInt("quality_max_size", recommended.optInt("max_size"))
                    .putInt("video_bit_rate", recommended.optInt("bitrate")).putString("bitrate_mode", recommended.optString("mode"))
                    .putInt("max_fps", 30).putInt("buffer_ms", Math.max(30, Math.min(80, recommended.optInt("buffer_ms", 80)))).apply();
            Toast.makeText(activity, "已保存，下次连接使用本次推荐", Toast.LENGTH_LONG).show();
        }));
        box.addView(button("返回连接页", this::leave));
        ScrollView scroll = new ScrollView(activity); scroll.addView(box); activity.setContentView(scroll);
    }

    static JSONObject recommendation(JSONArray stages) {
        if (stages == null) return null;
        // Only choose repeated comparable configurations; require every repeat to pass.
        Map<String, List<JSONObject>> groups = new LinkedHashMap<>();
        for (int i = 0; i < stages.length(); i++) {
            JSONObject s = stages.optJSONObject(i); if (s == null) continue;
            String key = s.optInt("max_size") + ":" + s.optInt("bitrate") + ":" + s.optString("mode");
            groups.computeIfAbsent(key, ignored -> new ArrayList<>()).add(s);
        }
        JSONObject best = null;
        for (List<JSONObject> group : groups.values()) {
            if (group.size() < 2) continue;
            boolean usable = true;
            for (JSONObject s : group) usable &= s.optBoolean("valid") && s.optBoolean("hardware_decoder")
                    && !s.optBoolean("network_changed") && !s.optBoolean("adaptive_rejected")
                    && s.optDouble("codec_callback_fps", s.optDouble("rendered_fps", 0)) >= 25.5 && s.optDouble("max_render_gap_ms", 9999) < 500
                    && s.optInt("thermal_end", 0) < PowerManager.THERMAL_STATUS_SEVERE;
            JSONObject s = group.get(0);
            if (usable && (best == null || s.optInt("max_size") < best.optInt("max_size")
                    || s.optInt("max_size") == best.optInt("max_size") && s.optInt("bitrate") < best.optInt("bitrate"))) best = s;
        }
        return best;
    }

    void retryUpload(JSONObject report) {
        resultStatus.setText("正在重新提交…"); worker.execute(() -> { upload(report); activity.runOnUiThread(() -> { if (!closed) updateReceipt(); }); });
    }
    void updateReceipt() { resultStatus.setText(reportId.isEmpty() ? "报告已保存在手机，尚未上传：" + uploadError + "\n可以重新提交，不必重测。"
            : "报告已上传到当前服务器\n报告编号：" + reportId + "\n维护者可以直接读取，不需要截图或复制数据。"); }
    Button button(String text, Runnable action) { Button b = new Button(activity); b.setText(text); CuteUi.style(b, CuteUi.MINT, true); b.setOnClickListener(v -> action.run()); return b; }

    static void previous(MainActivity activity) {
        try {
            String host = Endpoint.destination(activity.address.getText().toString()), name = activity.user.getText().toString(), secret = activity.password.getText().toString();
            android.content.SharedPreferences saved = activity.getSharedPreferences("connection", 0);
            if (!Endpoint.sameDestination(host, saved.getString("diagnostic_report_host", "")) || !name.equals(saved.getString("diagnostic_account", "")))
                throw new IllegalArgumentException("请使用上次检测的服务器和账号查看报告");
            if (secret.isEmpty()) throw new IllegalArgumentException("请先填写或保存密码，以便重新提交报告");
            activity.host = host; activity.initTLS();
            JSONObject report;
            try (InputStream in = activity.openFileInput(LAST_REPORT)) {
                ByteArrayOutputStream out = new ByteArrayOutputStream(); byte[] buffer = new byte[4096]; int n;
                while ((n = in.read(buffer)) > 0) { if (out.size() + n > 131072) throw new IOException("报告文件过大"); out.write(buffer, 0, n); }
                report = new JSONObject(new String(out.toByteArray(), StandardCharsets.UTF_8));
            }
            String auth = "Basic " + android.util.Base64.encodeToString((name + ":" + secret).getBytes(StandardCharsets.UTF_8), 2);
            DiagnosticRunner r = new DiagnosticRunner(activity, auth, name, "", false); r.finished = true; activity.diagnostics = r;
            r.reportId = saved.getString("diagnostic_uploaded_id", ""); r.showResult(report, "上次检测报告");
        } catch (Exception e) { activity.status.setText("暂不能查看报告：" + MainActivity.message(e)); }
    }

    String transport() {
        try {
            ConnectivityManager cm = (ConnectivityManager) activity.getSystemService(Context.CONNECTIVITY_SERVICE);
            NetworkCapabilities c = cm.getNetworkCapabilities(cm.getActiveNetwork());
            if (c == null) return "none";
            if (c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return "wifi";
            if (c.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) return "cellular";
            if (c.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) return "ethernet";
            if (c.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) return "vpn";
            return "other";
        } catch (Exception e) { return "unknown"; }
    }
    boolean vpn() { try { ConnectivityManager cm = (ConnectivityManager) activity.getSystemService(Context.CONNECTIVITY_SERVICE); NetworkCapabilities c = cm.getNetworkCapabilities(cm.getActiveNetwork()); return c != null && c.hasTransport(NetworkCapabilities.TRANSPORT_VPN); } catch (Exception e) { return false; } }
    long networkId() { try { Network n = ((ConnectivityManager) activity.getSystemService(Context.CONNECTIVITY_SERVICE)).getActiveNetwork(); return n == null ? 0 : n.getNetworkHandle(); } catch (Exception e) { return 0; } }
    int thermal() { try { return ((PowerManager) activity.getSystemService(Context.POWER_SERVICE)).getCurrentThermalStatus(); } catch (Exception e) { return -1; } }
    int battery() { try { int value = ((BatteryManager) activity.getSystemService(Context.BATTERY_SERVICE)).getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY); return value >= 0 && value <= 100 ? value : -1; } catch (Exception e) { return -1; } }
    double batteryTemperature() {
        try { android.content.Intent intent = activity.registerReceiver(null, new android.content.IntentFilter(android.content.Intent.ACTION_BATTERY_CHANGED));
            if (intent != null && intent.hasExtra(BatteryManager.EXTRA_TEMPERATURE)) { double c = intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) / 10.0; if (c >= -20 && c <= 100) return c; }
        } catch (Exception ignored) {} return Double.NaN;
    }
    int signalStrength() { try { ConnectivityManager cm = (ConnectivityManager) activity.getSystemService(Context.CONNECTIVITY_SERVICE); NetworkCapabilities c = cm.getNetworkCapabilities(cm.getActiveNetwork()); return c == null ? Integer.MIN_VALUE : c.getSignalStrength(); } catch (Exception e) { return Integer.MIN_VALUE; } }
    static double percentile(List<Double> values, double fraction) { if (values.isEmpty()) return Double.NaN; List<Double> sorted = new ArrayList<>(values); Collections.sort(sorted); return sorted.get(Math.min(sorted.size() - 1, (int) Math.ceil(fraction * sorted.size()) - 1)); }
    void leave() { close(); activity.diagnostics = null; activity.auth = null; activity.login(); }
    void close() {
        if (closed) return;
        if (!finished) cancel();
        closed = true; measured = null; worker.shutdown();
    }
}
