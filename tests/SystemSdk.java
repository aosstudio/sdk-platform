import android.provider.DeviceConfig;
import android.webkit.PacProcessor;
import android.webkit.WebViewDelegate;
import android.webkit.WebViewFactoryProvider;

public final class SystemSdk {
    WebViewFactoryProvider provider;
    WebViewDelegate delegate;

    String readProxyConfiguration() {
        return DeviceConfig.getProperty("webview", "proxy");
    }

    PacProcessor proxyProcessor() {
        return PacProcessor.getInstance();
    }
}
