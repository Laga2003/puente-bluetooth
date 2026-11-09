package com.practica.btpuente;

import android.Manifest;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

public class MainActivity extends Activity implements BridgeServer.Listener {
    private static final int REQUEST_PERMISSIONS = 1;
    static final String DEFAULT_PROMPT =
            "Devuelve únicamente lo que se pide en el texto que recibas, "
                    + "sin introducciones, comentarios, explicaciones ni despedidas. "
                    + "Si se pide escribir algo, escribe solo ese contenido. "
                    + "Responde en el idioma del texto recibido, salvo que se pida otro. "
                    + "Usa texto plano, sin markdown, sin tablas y sin emojis.";

    private SharedPreferences preferences;
    private EditText apiKeyField;
    private EditText promptField;
    private TextView logView;
    private Button startButton;
    private Button stopButton;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        preferences = getSharedPreferences("ajustes", MODE_PRIVATE);

        int padding = Math.round(16 * getResources().getDisplayMetrics().density);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(padding, padding, padding, padding);

        layout.addView(label("Clave de API de Groq"));
        apiKeyField = new EditText(this);
        apiKeyField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        apiKeyField.setText(preferences.getString("api_key", ""));
        layout.addView(apiKeyField);

        layout.addView(label("Prompt del sistema"));
        promptField = new EditText(this);
        promptField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        promptField.setText(preferences.getString("prompt", DEFAULT_PROMPT));
        layout.addView(promptField);

        startButton = new Button(this);
        startButton.setOnClickListener(view -> onStartPressed());
        layout.addView(startButton);

        stopButton = new Button(this);
        stopButton.setText("Detener servidor");
        stopButton.setOnClickListener(view -> onStopPressed());
        layout.addView(stopButton);

        logView = new TextView(this);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(logView);
        layout.addView(scroll, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        setContentView(layout);
    }

    @Override
    protected void onResume() {
        super.onResume();
        logView.setText("");
        BridgeService.attachLog(this);
        showRunning(BridgeService.isRunning());
    }

    @Override
    protected void onPause() {
        BridgeService.attachLog(null);
        super.onPause();
    }

    private TextView label(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        return view;
    }

    private void showRunning(boolean running) {
        startButton.setText(running ? "Guardar ajustes" : "Iniciar servidor");
        stopButton.setEnabled(running);
    }

    private void onStartPressed() {
        preferences.edit()
                .putString("api_key", apiKeyField.getText().toString().trim())
                .putString("prompt", promptField.getText().toString().trim())
                .apply();

        List<String> missing = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 31 && !hasPermission(Manifest.permission.BLUETOOTH_CONNECT)) {
            missing.add(Manifest.permission.BLUETOOTH_CONNECT);
        }
        if (Build.VERSION.SDK_INT >= 33 && !hasPermission(Manifest.permission.POST_NOTIFICATIONS)) {
            missing.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (!missing.isEmpty()) {
            requestPermissions(missing.toArray(new String[0]), REQUEST_PERMISSIONS);
            return;
        }
        startServer();
    }

    private boolean hasPermission(String permission) {
        return checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        if (requestCode != REQUEST_PERMISSIONS) return;
        if (Build.VERSION.SDK_INT < 31 || hasPermission(Manifest.permission.BLUETOOTH_CONNECT)) {
            startServer();
        } else {
            onLog("Sin permiso de Bluetooth no se puede iniciar el servidor");
        }
    }

    private void startServer() {
        if (BridgeService.isRunning()) {
            onLog("Ajustes guardados");
            return;
        }
        BluetoothManager manager = getSystemService(BluetoothManager.class);
        BluetoothAdapter adapter = manager == null ? null : manager.getAdapter();
        if (adapter == null || !adapter.isEnabled()) {
            onLog("Activa el Bluetooth y vuelve a intentarlo");
            return;
        }
        Intent service = new Intent(this, BridgeService.class);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(service);
        } else {
            startService(service);
        }
        showRunning(true);
    }

    private void onStopPressed() {
        stopService(new Intent(this, BridgeService.class));
        showRunning(false);
    }

    @Override
    public void onLog(String message) {
        runOnUiThread(() -> logView.append(message + "\n"));
    }
}
