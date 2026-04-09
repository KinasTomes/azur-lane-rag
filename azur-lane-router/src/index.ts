/**
 * Welcome to Cloudflare Workers! This is your first worker.
 *
 * - Run `npm run dev` in your terminal to start a development server
 * - Open a browser tab at http://localhost:8787/ to see your worker in action
 * - Run `npm run deploy` to publish your worker
 *
 * Bind resources to your worker in `wrangler.jsonc`. After adding bindings, a type definition for the
 * `Env` object can be regenerated with `npm run cf-typegen`.
 *
 * Learn more at https://developers.cloudflare.com/workers/
 */

export default {
	async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
		// Only accept POST
		if (request.method !== "POST") {
			return new Response("Use POST to send queries", { status: 405 });
		}

		try {
			const body = await request.json();
			const query = body?.query;

			if (!query) {
				return new Response(
					JSON.stringify({ error: "Missing 'query' field" }),
					{ status: 400, headers: { "content-type": "application/json" } }
				);
			}

			// Call AI Router (strict JSON enforced)
			const answer = await env.AI.run("@cf/meta/llama-3.1-8b-instruct", {
				messages: [
					{
						role: "system",
						content: `
You are a routing agent for an Azur Lane RAG system.

CRITICAL INSTRUCTION: 
- ALL values in the JSON output MUST be in English, regardless of the user's input language.
- Keywords must be optimized for searching on the English Azur Lane Wiki (e.g., ship names, skill names in English).

Rules:
- Any question about Azur Lane characters, ships, skills, stats, lore → MUST be classified as "intent": "rag"
- For "keywords": Translate the core entities from the user's query into English technical terms (e.g., "Lucky E", "Bismarck Zwei", "Light Armor").
- Output MUST be valid JSON, no markdown, no extra text.

Schema:
{
  "intent": "rag" | "chat",
  "keywords": "English search terms",
  "target": "kimi" | "qwen"
}
`
					},
					{
						role: "user",
						content: query
					}
				],
			});

			const raw = answer.response;

			// Try parsing directly (should work if model obeys)
			let parsed;
			try {
				parsed = JSON.parse(raw);
			} catch {
				// fallback (minimal, just in case)
				parsed = { raw };
			}

			return new Response(JSON.stringify(parsed), {
				headers: { "content-type": "application/json" },
			});

		} catch (err) {
			return new Response(
				JSON.stringify({ error: "Invalid request", detail: String(err) }),
				{ status: 500, headers: { "content-type": "application/json" } }
			);
		}
	},
} satisfies ExportedHandler<Env>;
