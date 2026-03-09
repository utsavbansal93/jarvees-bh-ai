/**
 * Jarvees AI Service
 *
 * A Node.js sidecar that combines two libraries:
 *   - claude-code-bridge  → calls Claude using Claude Code's OAuth token (no API key needed)
 *   - ai-model-cascade    → orchestrates the full model cascade with failure tracking
 *
 * Runs on port 3100.  Claude bridge runs on port 3099.
 * Python backend (main.py) calls POST /chat; falls back to its own cascade if this service
 * is not running.
 *
 * Start:  node --env-file-if-exists=../.env service.js
 */

import express from 'express';
import { createBridge } from 'claude-code-bridge';
import { createCascade } from 'ai-model-cascade';
import { GoogleGenerativeAI } from '@google/generative-ai';

const PORT        = parseInt(process.env.NODE_AI_PORT  || '3100');
const BRIDGE_PORT = parseInt(process.env.BRIDGE_PORT   || '3099');
const CLAUDE_MODEL = process.env.CLAUDE_MODEL || 'claude-sonnet-4-6';

// ── Claude via claude-code-bridge ─────────────────────────────────────────────
// The bridge exposes POST /generate on BRIDGE_PORT.
// It reads CLAUDE_CODE_OAUTH_TOKEN from env on every request, so token rotation
// across Claude Code sessions is handled automatically.

const { start: startBridge } = createBridge({ port: BRIDGE_PORT, verbose: false });

async function callClaude(systemPrompt, userPrompt) {
  const res = await fetch(`http://localhost:${BRIDGE_PORT}/generate`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({
      systemPrompt,
      userPrompt,
      model:     CLAUDE_MODEL,
      maxTokens: 2048,
    }),
  });
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data.text;
}

// ── Gemini via @google/generative-ai ─────────────────────────────────────────
// Mirrors the Python _gemini_response() logic: filters text parts explicitly
// to suppress thought_signature warnings from reasoning-enabled models.

const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY || '');

function makeGeminiCall(modelId) {
  return async (systemPrompt, userPrompt) => {
    const model  = genAI.getGenerativeModel({ model: modelId, systemInstruction: systemPrompt });
    const result = await model.generateContent(userPrompt);
    const parts  = result.response.candidates?.[0]?.content?.parts ?? [];
    const text   = parts
      .filter(p => typeof p.text === 'string' && p.text)
      .map(p => p.text)
      .join('')
      .trim();
    if (!text) throw new Error(`No text content returned by ${modelId}`);
    return text;
  };
}

// Gemini cascade order — matches Python GEMINI_CASCADE in chat_handler.py
const GEMINI_MODELS = [
  'gemini-3.1-flash-lite-preview',
  'gemini-3-flash-preview',
  'gemini-2.5-pro',
  'gemini-2.5-flash',
  'gemini-2.5-flash-lite',
  'gemini-2.5-flash-lite-preview-09-2025',
  'gemini-2.0-flash',
];

// ── ai-model-cascade setup ────────────────────────────────────────────────────
// createCascade() accepts model definitions with { id, label, call }.
// It handles failure classification (billing / quota / transient) and tracks
// which models are available — same logic Jarvees already has in Python,
// but now backed by the dedicated library.

const cascade = createCascade([
  { id: 'claude',  label: '✦ Claude',  call: callClaude },
  ...GEMINI_MODELS.map(id => ({
    id,
    label: `✦ Gemini ${id}`,
    call:  makeGeminiCall(id),
  })),
]);

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Normalise ai-model-cascade's cascadeLog into Jarvees' expected shape. */
function normaliseCascadeLog(log) {
  if (!Array.isArray(log)) return [];
  return log.map(entry => ({
    model:     entry.id    ?? entry.model ?? 'unknown',
    failed:    entry.failed ?? false,
    reason:    entry.reason ?? (entry.failed ? 'failed' : null),
    elapsed_s: entry.elapsed_ms != null
      ? Math.round(entry.elapsed_ms / 100) / 10   // ms → s, 1 dp
      : (entry.elapsed_s ?? 0),
  }));
}

// ── Express app ───────────────────────────────────────────────────────────────

const app = express();
app.use(express.json());

/**
 * POST /chat
 * Body:  { system_prompt: string, user_message: string }
 * Returns: { text, model, elapsed_ms, cascade_log }
 */
app.post('/chat', async (req, res) => {
  const { system_prompt = '', user_message } = req.body ?? {};
  if (!user_message) {
    return res.status(400).json({ error: 'user_message is required' });
  }

  try {
    const result = await cascade.run(system_prompt, user_message);
    res.json({
      text:        result.text,
      model:       result.model,
      elapsed_ms:  result.elapsed_ms,
      cascade_log: normaliseCascadeLog(result.cascadeLog),
    });
  } catch (err) {
    res.status(503).json({
      error:       err.message,
      cascade_log: normaliseCascadeLog(err.cascadeLog),
    });
  }
});

/**
 * GET /health
 * Returns cascade status for each model — mirrors Python's /api/model/status.
 */
app.get('/health', (_req, res) => {
  res.json({ ok: true, cascade: cascade.getStatus() });
});

/**
 * POST /reset
 * Body: { id?: string }  — omit id to reset all models.
 * Mirrors Python's /api/model/reset.
 */
app.post('/reset', (req, res) => {
  const { id } = req.body ?? {};
  if (id) {
    cascade.reset(id);
  } else {
    ['claude', ...GEMINI_MODELS].forEach(m => cascade.reset(m));
  }
  res.json({ ok: true, cascade: cascade.getStatus() });
});

// ── Start ─────────────────────────────────────────────────────────────────────

await startBridge();   // claude-code-bridge listens on BRIDGE_PORT
app.listen(PORT, () => {
  console.log(`[Jarvees AI Service] ready on http://localhost:${PORT}`);
  console.log(`[Claude Bridge]      ready on http://localhost:${BRIDGE_PORT}`);
  if (!process.env.CLAUDE_CODE_OAUTH_TOKEN) {
    console.warn('[Claude Bridge]      CLAUDE_CODE_OAUTH_TOKEN not set — Claude calls will fail until token is available.');
  }
});
