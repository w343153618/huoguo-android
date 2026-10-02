package local.remoteandroid.direct;
/** UI bridge; implementation is compiled only in the isolated UDP candidate. */
public interface LanUdpEntry {
    void showLogin();
    boolean active();
    void cancel(boolean showLogin);
    void failed(Exception failure);
}
