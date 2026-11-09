package com.practica.btpuente;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Llamada a la API de Groq, compatible con la de OpenAI. */
final class GroqClient {
    static final String DEFAULT_URL = "https://api.groq.com/openai/v1/chat/completions";
    private static final String MODEL = "openai/gpt-oss-120b";
    private static final int MAX_COMPLETION_TOKENS = 4096;

    private final String endpoint;

    GroqClient(String endpoint) {
        this.endpoint = endpoint;
    }

    String complete(String apiKey, String systemPrompt, String userText) throws IOException, JSONException {
        JSONArray messages = new JSONArray()
                .put(new JSONObject().put("role", "system").put("content", systemPrompt))
                .put(new JSONObject().put("role", "user").put("content", userText));
        JSONObject body = new JSONObject()
                .put("model", MODEL)
                .put("messages", messages)
                .put("max_completion_tokens", MAX_COMPLETION_TOKENS);

        HttpURLConnection connection = (HttpURLConnection) new URL(endpoint).openConnection();
        try {
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(120000);
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setRequestProperty("Authorization", "Bearer " + apiKey);
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty("User-Agent", "PuenteBluetooth/1.0");
            try (OutputStream out = connection.getOutputStream()) {
                out.write(body.toString().getBytes(StandardCharsets.UTF_8));
            }

            int status = connection.getResponseCode();
            InputStream stream = status < 400 ? connection.getInputStream() : connection.getErrorStream();
            String response = stream == null ? "" : readAll(stream);
            if (status >= 400) {
                throw new IOException(describeError(status, response));
            }

            JSONObject choice = new JSONObject(response).getJSONArray("choices").getJSONObject(0);
            String content = choice.getJSONObject("message").optString("content", "").trim();
            if (content.isEmpty()) {
                throw new IOException("El modelo no ha devuelto texto (" + choice.optString("finish_reason") + ")");
            }
            return content;
        } finally {
            connection.disconnect();
        }
    }

    private static String describeError(int status, String response) {
        if (status == 401) return "Clave de API no válida";
        if (status == 429) return "Límite de uso de Groq alcanzado";
        try {
            return "Groq " + status + ": " + new JSONObject(response).getJSONObject("error").getString("message");
        } catch (JSONException e) {
            return "Groq ha respondido con el error " + status;
        }
    }

    private static String readAll(InputStream stream) throws IOException {
        try (InputStream in = stream) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int read;
            while ((read = in.read(buffer)) != -1) {
                out.write(buffer, 0, read);
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }
}
