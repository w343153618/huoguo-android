package local.remoteandroid.direct;

import java.math.BigDecimal;

final class Bitrate {
    private Bitrate() {}
    static int parseMbps(String value) {
        try {
            // Do not silently round a value above the encoder limit.
            BigDecimal mbps = new BigDecimal(value.trim());
            if (mbps.compareTo(new BigDecimal("0.5")) < 0 || mbps.compareTo(new BigDecimal("40")) > 0)
                throw new IllegalArgumentException();
            return mbps.multiply(new BigDecimal("1000000")).intValueExact();
        } catch (ArithmeticException | IllegalArgumentException e) {
            throw new IllegalArgumentException("请输入 0.5–40 Mbps 的码率，最多六位小数");
        }
    }
}
