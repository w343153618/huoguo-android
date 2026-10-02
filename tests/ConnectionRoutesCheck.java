package local.remoteandroid.direct;

import java.util.Arrays;

/** Fixed route compatibility never expands old passwords to arbitrary host ports. */
public final class ConnectionRoutesCheck {
    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
    private static boolean alias(String destination, String user, String oldKey) {
        return Arrays.asList(ConnectionRoutes.passwordAliases(destination, user)).contains(oldKey);
    }
    public static void main(String[] args) {
        check(ConnectionRoutes.TAILSCALE_ENDPOINT.equals("100.65.0.2:15556"), "yilufa M1 shortcut not selected");
        check(ConnectionRoutes.M1_ENDPOINT.equals("146.56.249.175:15556"), "public M1 default changed");
        check(ConnectionRoutes.M5_ENDPOINT.equals("146.56.249.175:15558"), "public M5 backup changed");
        check(ConnectionRoutes.migratedShortcut("100.64.0.2").equals("100.65.0.2"), "old host shortcut not migrated");
        check(ConnectionRoutes.migratedShortcut("100.64.0.2:15556").equals("100.65.0.2:15556"), "old host:port shortcut not migrated");
        check(ConnectionRoutes.migratedShortcut(ConnectionRoutes.migratedShortcut("100.64.0.2:15556"))
                .equals("100.65.0.2:15556"), "reopening app reverses migration");
        for (String host : new String[]{"192.168.9.99", "192.168.9.126:15556", "example.com", "100.64.0.20",
                "100.64.0.2:8089", "100.64.0.2:15558", " 100.64.0.2", "100.65.0.2",
                "100.65.0.2:15556", "146.56.249.175:15558", ""})
            check(ConnectionRoutes.migratedShortcut(host).equals(host), "custom address unexpectedly changed: " + host);
        for (String destination : new String[]{ConnectionRoutes.M1_ENDPOINT, ConnectionRoutes.M5_ENDPOINT,
                ConnectionRoutes.TAILSCALE_ENDPOINT}) {
            check(alias(destination, "huoguo", ConnectionRoutes.PUBLIC_HOST), "fixed route cannot restore authorized old public account");
            check(alias(destination, "huoguo", ConnectionRoutes.LEGACY_TAILSCALE_HOST), "fixed route cannot restore old tailnet account");
            check(alias(destination, "huoguo", ConnectionRoutes.LEGACY_TAILSCALE_HOST + ":15556"),
                    "fixed route cannot restore endpoint-bound old tailnet account");
            check(!alias(destination, "huoguo", Endpoint.identity(destination)), "primary endpoint repeated as an alias");
        }
        check(alias(ConnectionRoutes.M1_ENDPOINT, "huoguo", ConnectionRoutes.M5_ENDPOINT)
                && alias(ConnectionRoutes.M5_ENDPOINT, "huoguo", ConnectionRoutes.M1_ENDPOINT),
                "explicitly authorized M1/M5 endpoint-bound account migration unavailable");
        for (String destination : new String[]{"146.56.249.175:8089", "146.56.249.175:15557",
                "100.64.0.2:15558", "100.65.0.2:15558", "example.com:15558", "146.56.249.175.evil:15558"})
            check(ConnectionRoutes.passwordAliases(destination, "huoguo").length == 0,
                    "old bare-host password leaks into an unauthorized port or host: " + destination);
        for (String host : new String[]{"192.168.9.128", "example.com", "146.56.249.175.evil", "100.64.0.2"})
            check(Arrays.equals(ConnectionRoutes.passwordAliases(host, "huoguo"), new String[]{host}),
                    "custom default-port compatibility expanded beyond its own host");
        for (String account : new String[]{"wyw", "Huoguo", "", "other"}) {
            check(ConnectionRoutes.passwordAliases(ConnectionRoutes.M5_ENDPOINT, account).length == 0,
                    "cross-port password reuse expanded beyond fixed huoguo account");
            check(Arrays.equals(ConnectionRoutes.passwordAliases(ConnectionRoutes.M1_ENDPOINT, account),
                    new String[]{ConnectionRoutes.PUBLIC_HOST}), "old same-host default-port account lost");
            check(Arrays.equals(ConnectionRoutes.passwordAliases(ConnectionRoutes.TAILSCALE_ENDPOINT, account),
                    new String[]{ConnectionRoutes.TAILSCALE_HOST}), "old tailnet password reuse expanded beyond huoguo");
        }
        check(!ConnectionRoutes.componentProbeAllowed("local.remoteandroid.direct", true), "formal app honors probe blank-screen extra");
        check(!ConnectionRoutes.componentProbeAllowed("local.remoteandroid.direct.experiment", false), "experiment enters probe without explicit request");
        check(ConnectionRoutes.componentProbeAllowed("local.remoteandroid.direct.experiment", true), "isolated component probe disabled");
        System.out.println("ConnectionRoutesCheck PASS: exact migration, authorized fixed endpoints, old AAD bounds and probe gate");
    }
}
