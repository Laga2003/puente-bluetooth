package com.practica.btpuente;

import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothServerSocket;
import android.bluetooth.BluetoothSocket;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Servidor RFCOMM: recibe enunciados, llama al modelo y entrega la respuesta letra a letra. */
@SuppressLint("MissingPermission")
final class BridgeServer {
    interface Listener {
        void onLog(String message);
    }

    interface Settings {
        String apiKey();
        String systemPrompt();
        void setSystemPrompt(String prompt);
    }

    private static final UUID SERVICE_UUID = UUID.fromString("7c1d6f2a-4b7e-4f0e-9a55-2e6b8c3d1a90");
    private static final int MAX_FRAME = 1024 * 1024;

    private final BluetoothAdapter adapter;
    private final GroqClient groq;
    private final Settings settings;
    private final Listener listener;
    private final ExecutorService promptExecutor = Executors.newSingleThreadExecutor();

    private volatile boolean running;
    private volatile BluetoothServerSocket serverSocket;
    private volatile BluetoothSocket client;
    private volatile DataOutputStream output;
    private volatile int latestPromptId = -1;
    private volatile int responseId = -1;
    private volatile int[] response = new int[0];

    BridgeServer(BluetoothAdapter adapter, GroqClient groq, Settings settings, Listener listener) {
        this.adapter = adapter;
        this.groq = groq;
        this.settings = settings;
        this.listener = listener;
    }

    void start() {
        if (running) return;
        running = true;
        new Thread(this::acceptLoop, "bt-accept").start();
    }

    void stop() {
        running = false;
        closeQuietly(serverSocket);
        closeQuietly(client);
        promptExecutor.shutdownNow();
    }

    private void acceptLoop() {
        try {
            serverSocket = adapter.listenUsingRfcommWithServiceRecord("PuenteBluetooth", SERVICE_UUID);
            listener.onLog("Esperando al PC…");
            while (running) {
                BluetoothSocket socket = serverSocket.accept();
                serve(socket);
                if (running) listener.onLog("PC desconectado. Esperando…");
            }
        } catch (IOException e) {
            if (running) listener.onLog("Error de Bluetooth: " + e.getMessage());
        } finally {
            running = false;
            closeQuietly(serverSocket);
        }
    }

    private void serve(BluetoothSocket socket) {
        client = socket;
        try {
            DataInputStream input = new DataInputStream(socket.getInputStream());
            output = new DataOutputStream(socket.getOutputStream());
            listener.onLog("PC conectado");
            while (running) {
                handle(readFrame(input));
            }
        } catch (IOException | JSONException e) {
            if (running) listener.onLog("Conexión cerrada: " + e.getMessage());
        } finally {
            output = null;
            closeQuietly(socket);
        }
    }

    private void handle(JSONObject message) throws IOException, JSONException {
        switch (message.getString("t")) {
            case "hello":
                send(new JSONObject().put("t", "hello_ok").put("v", 1));
                break;
            case "prompt":
                int id = message.getInt("id");
                String text = message.getString("text");
                latestPromptId = id;
                promptExecutor.execute(() -> answer(id, text));
                break;
            case "next":
                sendLetter(message.getInt("id"), message.getInt("seq"));
                break;
            case "get_prompt":
                sendPrompt(false);
                break;
            case "set_prompt":
                String prompt = message.getString("text").trim();
                if (prompt.isEmpty()) {
                    send(new JSONObject().put("t", "error").put("text", "El prompt no puede estar vacío"));
                } else {
                    settings.setSystemPrompt(prompt);
                    listener.onLog("Prompt actualizado desde el PC");
                    sendPrompt(true);
                }
                break;
            default:
                break;
        }
    }

    private void sendPrompt(boolean saved) throws IOException, JSONException {
        send(new JSONObject().put("t", "prompt_value").put("text", settings.systemPrompt()).put("saved", saved));
    }

    private void sendLetter(int id, int seq) throws IOException, JSONException {
        int[] letters = response;
        JSONObject reply = new JSONObject().put("id", id);
        if (id != responseId || seq < 0 || seq >= letters.length) {
            reply.put("t", "end");
        } else {
            reply.put("t", "char").put("seq", seq)
                    .put("c", new String(Character.toChars(letters[seq])));
        }
        send(reply);
    }

    private void answer(int id, String text) {
        if (id != latestPromptId) return;
        listener.onLog("Enunciado recibido (" + text.length() + " caracteres)");
        try {
            send(new JSONObject().put("t", "status").put("text", "Consultando al modelo…"));
            String apiKey = settings.apiKey();
            if (apiKey.isEmpty()) throw new IOException("Falta la clave de API en la app");
            String reply = groq.complete(apiKey, settings.systemPrompt(), text).replace("\r\n", "\n");
            if (id != latestPromptId) return;
            int[] letters = reply.codePoints().toArray();
            response = letters;
            responseId = id;
            send(new JSONObject().put("t", "ready").put("id", id).put("len", letters.length));
            listener.onLog("Respuesta lista (" + letters.length + " letras)");
        } catch (IOException | JSONException e) {
            listener.onLog("Error: " + e.getMessage());
            try {
                send(new JSONObject().put("t", "error").put("text", String.valueOf(e.getMessage())));
            } catch (IOException | JSONException ignored) {
                // El PC ya no está conectado.
            }
        }
    }

    private static JSONObject readFrame(DataInputStream input) throws IOException, JSONException {
        int size = input.readInt();
        if (size < 0 || size > MAX_FRAME) throw new IOException("Mensaje de tamaño no válido");
        byte[] data = new byte[size];
        input.readFully(data);
        return new JSONObject(new String(data, StandardCharsets.UTF_8));
    }

    private void send(JSONObject message) throws IOException {
        DataOutputStream out = output;
        if (out == null) throw new IOException("PC no conectado");
        byte[] data = message.toString().getBytes(StandardCharsets.UTF_8);
        byte[] frame = new byte[4 + data.length];
        frame[0] = (byte) (data.length >>> 24);
        frame[1] = (byte) (data.length >>> 16);
        frame[2] = (byte) (data.length >>> 8);
        frame[3] = (byte) data.length;
        System.arraycopy(data, 0, frame, 4, data.length);
        synchronized (this) {
            out.write(frame);
            out.flush();
        }
    }

    private static void closeQuietly(java.io.Closeable closeable) {
        try {
            if (closeable != null) closeable.close();
        } catch (IOException ignored) {
            // Nada que hacer al cerrar.
        }
    }
}
