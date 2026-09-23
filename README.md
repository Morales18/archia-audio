# archia-audio

Generador de podcasts técnicos para ArchIA.

## Flujo

```text
ChatGPT
  -> requests/Bxx.json
  -> GitHub Actions
  -> TTS provider
     -> Azure Speech (principal, si hay credenciales)
     -> Edge TTS (fallback gratuito)
  -> ffmpeg mastering
  -> MP3 artifact
  -> ChatGPT
```

## Preset actual

- Español de España: `es-ES`
- Ritmo por defecto: `+4%`
- Pausas entre párrafos: 150 ms
- Inglés técnico: `en-GB`
- Mastering: loudness normalizado, 48 kHz, mono, 192 kbps
- Glosario: `config/technical_terms.json`
- Voz Azure recomendada: `es-ES-XimenaMultilingualNeural`
- Voz Edge fallback: `es-ES-XimenaNeural`

## Providers

### Azure Speech

Es el provider preferido para el podcast final porque permite SSML y cambio explícito de idioma para términos como:

- Quality Gate
- Agent Runtime
- workflow
- retry
- Pull Request
- Tool Request
- fail closed

El script genera automáticamente SSML con `<lang xml:lang="en-GB">` para los términos definidos en el glosario.

Para activarlo, configura estos GitHub Actions secrets:

- `AZURE_SPEECH_KEY`
- `AZURE_SPEECH_REGION`

No guardes nunca la key en el repositorio.

### Edge TTS

Si Azure no está configurado, el pipeline sigue funcionando automáticamente con Edge TTS.

No requiere API key.

Es útil como fallback y para pruebas, aunque tiene menos control de pronunciación bilingüe y prosodia.

## Request de ejemplo

```json
{
  "block": "B03",
  "provider": "auto",
  "locale": "es-ES",
  "english_locale": "en-GB",
  "rate": "+4%",
  "paragraph_pause_ms": 150,
  "azure_voice": "es-ES-XimenaMultilingualNeural",
  "edge_voice": "es-ES-XimenaNeural",
  "chapters": []
}
```

## Generación

Cada cambio en `requests/*.json` dispara:

`.github/workflows/generate-podcast.yml`

El resultado se publica como artifact:

`archia-podcast-<commit-sha>`

## Diseño

ArchIA conserva su propio formato de request y el provider es intercambiable. La generación de contenido no depende de Azure ni de Edge.
