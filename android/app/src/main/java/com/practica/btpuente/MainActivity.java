package com.practica.btpuente;

import android.Manifest;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public class MainActivity extends Activity implements BridgeServer.Listener, BridgeServer.Settings {
    private static final int REQUEST_BLUETOOTH = 1;
    private static final String DEFAULT_PROMPT =
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
    private BridgeServer server;
    private volatile String apiKey = "";
    private volatile String systemPrompt = DEFAULT_PROMPT;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
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
        startButton.setText("Iniciar servidor");
        startButton.setOnClickListener(view -> onStartPressed());
        layout.addView(startButton);

        logView = new TextView(this);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(logView);
        layout.addView(scroll, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        setContentView(layout);
    }

    private TextView label(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        return view;
    }

    private void onStartPressed() {
        apiKey = apiKeyField.getText().toString().trim();
        systemPrompt = promptField.getText().toString().trim();
        preferences.edit().putString("api_key", apiKey).putString("prompt", systemPrompt).apply();

        if (Build.VERSION.SDK_INT >= 31
                && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT}, REQUEST_BLUETOOTH);
            return;
        }
        startServer();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        if (requestCode != REQUEST_BLUETOOTH) return;
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
            startServer();
        } else {
            onLog("Sin permiso de Bluetooth no se puede iniciar el servidor");
        }
    }

    private void startServer() {
        if (server != null) {
            onLog("Ajustes guardados");
            return;
        }
        BluetoothManager manager = getSystemService(BluetoothManager.class);
        BluetoothAdapter adapter = manager == null ? null : manager.getAdapter();
        if (adapter == null || !adapter.isEnabled()) {
            onLog("Activa el Bluetooth y vuelve a intentarlo");
            return;
        }
        server = new BridgeServer(adapter, new GroqClient(GroqClient.DEFAULT_URL), this, this);
        server.start();
        startButton.setText("Guardar ajustes");
    }

    @Override
    public String apiKey() {
        return apiKey;
    }

    @Override
    public String systemPrompt() {
        return systemPrompt;
    }

    @Override
    public void onLog(String message) {
        runOnUiThread(() -> logView.append(message + "\n"));
    }

    @Override
    protected void onDestroy() {
        if (server != null) server.stop();
        super.onDestroy();
    }
}
