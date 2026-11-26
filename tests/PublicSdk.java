import android.app.Activity;
import android.bluetooth.BluetoothDevice;
import android.os.Build;

public final class PublicSdk extends Activity {
    int apiLevel() {
        return Build.VERSION_CODES.CINNAMON_BUN;
    }

    String address(BluetoothDevice device) {
        return device.getAddress();
    }
}
