package com.commonwealthrobotics.kloster;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import javax.net.ssl.*;

/** The same pupil endpoint as Printværksted; no printer or teacher credentials. */
public final class PrintQueueClient {
    private static final Gson JSON = new Gson();
    private final URI base;
    private final SSLSocketFactory tls;

    public PrintQueueClient(URI base, Path ca) throws Exception {
        if (!java.util.Set.of("http", "https").contains(base.getScheme()) || base.getHost() == null
                || base.getUserInfo() != null || base.getQuery() != null || base.getFragment() != null
                || !(base.getPath().isEmpty() || base.getPath().equals("/")))
            throw new IOException("Printværkstedets adresse er ikke gyldig.");
        this.base = base;
        if (ca != null) {
            KeyStore trust = KeyStore.getInstance(KeyStore.getDefaultType());
            trust.load(null, null);
            try (InputStream input = Files.newInputStream(ca)) {
                int i = 0;
                for (var cert : CertificateFactory.getInstance("X.509").generateCertificates(input))
                    trust.setCertificateEntry("elevhub-" + i++, cert);
            }
            TrustManagerFactory managers = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
            managers.init(trust);
            SSLContext context = SSLContext.getInstance("TLS");
            context.init(null, managers.getTrustManagers(), null);
            tls = context.getSocketFactory();
        } else tls = null;
    }

    public static PrintQueueClient configured() throws Exception {
        Path config = Path.of("/etc/elevhub/cadoodle-print.json");
        if (!Files.isRegularFile(config)) throw new IOException("Printværkstedet er ikke tilsluttet. Bed en voksen om hjælp.");
        JsonObject data = JSON.fromJson(Files.readString(config), JsonObject.class);
        return new PrintQueueClient(URI.create(data.get("url").getAsString()),
                data.has("ca_file") ? Path.of(data.get("ca_file").getAsString()) : null);
    }

    public String endpoint() { return base.toString(); }

    public JsonObject submit(Path stl, String name, String requestId) throws Exception {
        long length = Files.size(stl);
        if (length < 1 || length > 25 * 1024 * 1024) throw new IOException("Modellen må højst fylde 25 MB.");
        HttpURLConnection connection = connection("/api/jobs");
        connection.setRequestMethod("POST");
        connection.setDoOutput(true);
        connection.setFixedLengthStreamingMode(length);
        connection.setRequestProperty("Content-Type", "application/octet-stream");
        connection.setRequestProperty("X-Filename", URLEncoder.encode(name, StandardCharsets.UTF_8).replace("+", "%20"));
        connection.setRequestProperty("X-Colour", "any");
        connection.setRequestProperty("X-Quality", "standard");
        connection.setRequestProperty("X-Request-ID", requestId);
        try {
            try (OutputStream output = connection.getOutputStream()) { Files.copy(stl, output); }
            JsonObject result = response(connection);
            if (!result.has("id") || !result.get("id").getAsString().matches("[a-zA-Z0-9-]{1,80}"))
                throw new IOException("Printværkstedet svarede uden en kvittering.");
            return result;
        } finally { connection.disconnect(); }
    }

    public JsonObject state() throws Exception {
        HttpURLConnection connection = connection("/api/state");
        try { return response(connection); } finally { connection.disconnect(); }
    }

    private HttpURLConnection connection(String path) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) base.resolve(path).toURL().openConnection();
        connection.setConnectTimeout(10000);
        connection.setReadTimeout(30000);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Accept", "application/json");
        if (tls != null && connection instanceof HttpsURLConnection secure) secure.setSSLSocketFactory(tls);
        return connection;
    }

    private static JsonObject response(HttpURLConnection connection) throws Exception {
        int code = connection.getResponseCode();
        InputStream stream = code < 400 ? connection.getInputStream() : connection.getErrorStream();
        JsonObject data;
        try (stream) {
            if (stream == null) throw new IOException("Printværkstedet svarede ikke.");
            byte[] bytes = stream.readNBytes(2 * 1024 * 1024 + 1);
            if (bytes.length > 2 * 1024 * 1024) throw new IOException("Svaret fra printkøen er for stort.");
            data = JSON.fromJson(new String(bytes, StandardCharsets.UTF_8), JsonObject.class);
        }
        if (data == null) throw new IOException("Svaret fra printkøen kunne ikke læses.");
        if (code < 200 || code >= 300) {
            String message = data.has("error") ? data.get("error").getAsString() : "Printværkstedet afviste indsendelsen.";
            throw new Rejected(message, code >= 400 && code < 500);
        }
        return data;
    }

    public static final class Rejected extends IOException {
        public final boolean definite;
        Rejected(String message, boolean definite) { super(message); this.definite = definite; }
    }
}
