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
		if (request.method !== 'POST') {
			return new Response('Method Not Allowed', { status: 405 });
		}

		try {
			const body = await request.json();
			const query = body?.query;

			if (!query) {
				return new Response(JSON.stringify({ error: "Missing 'query' field" }), {
					status: 400,
					headers: { 'content-type': 'application/json' },
				});
			}

			const answer: any = await env.AI.run('@cf/zai-org/glm-4.7-flash', {
				messages: [
					{
						role: 'system',
						content: `You are the Dispatcher (Tier 1) for the AzurLaneRAG system.
Your task is to analyze the user's query and route it to the correct execution path.

CORE RULES:
- ALL output values must be in English.
- Optimized search terms must use official English Azur Lane Wiki terminology.
- You MUST return ONLY a valid JSON object. No markdown, no preambles.

INTENT TYPES:
- fact_check: Specific ship stats, rarity, or fixed data.
- strategy_synergy: Team building, equipment optimization, or synergy between ships.
- character_lore: Personalities, voice lines, or story background.
- meta_comparison: Comparing performance or efficiency between multiple entities.

ALLOWED MODELS (STRICT ENUM):
- HEAVY_THINKERS: "qwen3.5_397b", "qwen3_235b", "kimi_k2.5"
- REASONING_THINKERS: "qwq_32b", "ds_r1_qwen_32b", "qwen3_80b_thinking"
- FAST_THINKERS: "glm_4.7_flash", "qwen3_30b", "qwen2.5_7b"
- SYNTHESIZERS: "nemotron_super", "kimi_k2.5", "qwen3.5_397b", "qwen3_30b"

OUTPUT SCHEMA (STRICT):
{
  "intent": "fact_check" | "strategy_synergy" | "character_lore" | "meta_comparison",
  "complexity": "easy" | "medium" | "hard",
  "reasoning": "string",
  "execution_plan": {
    "primary_tool": "vector" | "sql" | "graph",
    "steps": [{ "action": "string", "query": "string" }],
    "thinker_model_required": "string_from_allowed_list"
  },
  "synthesizer_config": {
    "style": "naval_analyst" | "character_voice" | "concise_reporter",
    "model": "string_from_allowed_list"
  }
}

EXAMPLE OF CORRECT OUTPUT:
User: "Enterprise and Taihou AVI comparison"
Response:
{
  "intent": "meta_comparison",
  "complexity": "medium",
  "reasoning": "Comparing AVI stats of two carriers.",
  "execution_plan": {
    "primary_tool": "sql",
    "steps": [{ "action": "query_sql", "query": "SELECT name, avi FROM stats WHERE name IN ('Enterprise', 'Taihou')" }],
    "thinker_model_required": "ds_r1_qwen_32b"
  },
  "synthesizer_config": {
    "style": "concise_reporter",
    "model": "qwen3_30b"
  }
}

CRITICAL: Return ONLY the JSON object. Do NOT use markdown code blocks. NO reasoning_content.`,
					},
					{
						role: 'user',
						content: query,
					},
				],
			});

			// Extract text from standard Chat Completion or fallback
			let responseText = '';
			if (answer?.choices?.[0]?.message?.content) {
				responseText = answer.choices[0].message.content;
			} else if (typeof answer === 'string') {
				responseText = answer;
			} else {
				responseText = answer?.response || answer?.answer || JSON.stringify(answer);
			}

			console.log('--- Raw Dispatcher Response ---');
			console.log(responseText);
			console.log('-------------------------------');

			let parsed;

			try {
				// More robust JSON extraction
				const jsonStart = responseText.indexOf('{');
				const jsonEnd = responseText.lastIndexOf('}');

				if (jsonStart !== -1 && jsonEnd !== -1) {
					const jsonString = responseText.substring(jsonStart, jsonEnd + 1);
					parsed = JSON.parse(jsonString);
				} else {
					throw new Error('No JSON object found in response');
				}
			} catch (e) {
				parsed = {
					error: 'Failed to parse dispatcher response',
					message: String(e),
					raw: responseText,
				};
			}

			return new Response(JSON.stringify(parsed), {
				headers: { 'content-type': 'application/json' },
			});
		} catch (err) {
			return new Response(JSON.stringify({ error: 'Internal Server Error', detail: String(err) }), {
				status: 500,
				headers: { 'content-type': 'application/json' },
			});
		}
	},
} satisfies ExportedHandler<Env>;
