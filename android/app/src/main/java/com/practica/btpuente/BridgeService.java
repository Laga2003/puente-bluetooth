package com.practica.btpuente;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import java.util.ArrayDeque;

/** Mantiene el servidor Bluetooth activo aunque la pantalla esté apagada. */
public class BridgeService extends Service implements BridgeServer.Listener, BridgeServer.Settings {
    private static final String CHANNEL_ID = "servidor";
    private static final int NOTIFICATION_ID = 1;
    private static final int MAX_HISTORY = 100;

    private static final ArrayDeque<String> HISTORY = new ArrayDeque<>();
    private static BridgeServer.Listener logListener;
    private static volatile boolean running;

    private SharedPreferences preferences;
    private PowerManager.WakeLock wakeLock;
    private BridgeServer server;

    static boolean isRunning() {
        return running;
    }

    static synchronized void attachLog(BridgeServer.Listener listener) {
        logListener = listener;
        if (listener != null) {
            for (String line : HISTORY) {
                listener.onLog(line);
            }
        }
    }

    private static synchronized void addLog(String message) {
        HISTORY.addLast(message);
        while (HISTORY.size() > MAX_HISTORY) {
            HISTORY.removeFirst();
        }
        if (logListener != null) {
            logListener.onLog(message);
        }
    }

    private static synchronized void clearLog() {
        HISTORY.clear();
    }

    @Override
    public void onCreate() {
        super.onCreate();
        running = true;
        clearLog();
        preferences = getSharedPreferences("ajustes", MODE_PRIVATE);
        startForeground(NOTIFICATION_ID, buildNotification());

        PowerManager powerManager = (PowerManager) getSystemService(Context.POWER_SERVICE);
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "puente:servidor");
        wakeLock.acquire();

        BluetoothManager manager = getSystemService(BluetoothManager.class);
        BluetoothAdapter adapter = manager == null ? null : manager.getAdapter();
        if (adapter == null || !adapter.isEnabled()) {
            addLog("Activa el Bluetooth y vuelve a iniciar el servidor");
            stopSelf();
            return;
        }
        server = new BridgeServer(adapter, new GroqClient(GroqClient.DEFAULT_URL), this, this);
        server.start();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        if (server != null) {
            server.stop();
        }
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        addLog("Servidor detenido");
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public String apiKey() {
        return preferences.getString("api_key", "");
    }

    @Override
    public String systemPrompt() {
        return preferences.getString("prompt", MainActivity.DEFAULT_PROMPT);
    }

    @Override
    public void onLog(String message) {
        addLog(message);
    }

    private Notification buildNotification() {
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID, "Servidor Bluetooth", NotificationManager.IMPORTANCE_LOW);
            getSystemService(NotificationManager.class).createNotificationChannel(channel);
            builder = new Notification.Builder(this, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(this);
        }
        PendingIntent openApp = PendingIntent.getActivity(
                this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        return builder
                .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
                .setContentTitle("Puente Bluetooth")
                .setContentText("Servidor activo")
                .setContentIntent(openApp)
                .setOngoing(true)
                .build();
    }
}
