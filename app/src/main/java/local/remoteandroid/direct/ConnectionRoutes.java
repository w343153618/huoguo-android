package local.remoteandroid.direct;

import java.util.LinkedHashSet;

/** Only the project's fixed old shortcut is migrated; arbitrary user hosts stay intact. */
final class ConnectionRoutes {
    static final String PUBLIC_HOST = "146.56.249.175";
    static final String TAILSCALE_HOST = "100.64.0.2";
    static final String LEGACY_TAILSCALE_HOST = "100.65.0.2";
    static final String M1_ENDPOINT = PUBLIC_HOST + ":15556";
    static final String M5_ENDPOINT = PUBLIC_HOST + ":15558";
    static final String TAILSCALE_ENDPOINT = TAILSCALE_HOST + ":15556";

    static String migratedShortcut(String savedAddress) {
        if (LEGACY_TAILSCALE_HOST.equals(savedAddress)) return TAILSCALE_HOST;
        if ((LEGACY_TAILSCALE_HOST + ":15556").equals(savedAddress)) return TAILSCALE_HOST + ":15556";
        return savedAddress;
    }

    /**
     * The caller first loads the full endpoint identity. Old default-port keys
     * may still be decrypted with their original bare-host AAD. Cross-server
     * reuse is restricted to huoguo and the three fixed, owner-authorized routes.
     */
    static String[] passwordAliases(String destination, String user) {
        Endpoint.Address target = Endpoint.parse(destination);
        String identity = target.identity();
        LinkedHashSet<String> aliases = new LinkedHashSet<>();
        if (target.port == Endpoint.DEFAULT_PORT) aliases.add(target.host);
        boolean fixed = identity.equals(M1_ENDPOINT) || identity.equals(M5_ENDPOINT)
                || identity.equals(TAILSCALE_ENDPOINT);
        if ("huoguo".equals(user) && fixed) {
            aliases.add(M1_ENDPOINT); aliases.add(M5_ENDPOINT); aliases.add(TAILSCALE_ENDPOINT);
            aliases.add(LEGACY_TAILSCALE_HOST + ":15556");
            aliases.add(PUBLIC_HOST); aliases.add(TAILSCALE_HOST); aliases.add(LEGACY_TAILSCALE_HOST);
        }
        aliases.remove(identity);
        return aliases.toArray(new String[0]);
    }

    static boolean componentProbeAllowed(String applicationId, boolean requested) {
        return requested && "local.remoteandroid.direct.experiment".equals(applicationId);
    }
}
