export default {
	async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
		if (request.method !== 'POST') {
			return new Response('Method Not Allowed', { status: 405 });
		}

		try {
			const body: any = await request.json();
			const { query, role = 'dispatch', messages } = body;

			// TẦNG 1: DISPATCHER (GLM-4.7-Flash) - Giữ logic cố định
			if (role === 'dispatch') {
				if (!query) return new Response(JSON.stringify({ error: "Missing 'query' for dispatch" }), { status: 400 });
				
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
- HEAVY_THINKERS: "deepseek_v3.2" (Extreme Logic/685B), "qwen3.5_397b", "kimi_k2.5"
- REASONING_THINKERS: "deepseek_v3.1" (Fast Reasoning), "qwq_32b", "ds_r1_qwen_32b"
- FAST_THINKERS: "glm_4.7_flash", "qwen3_30b_fp8"
- SYNTHESIZERS: "nemotron_super", "deepseek_v3.1", "kimi_k2.5", "qwen3.5_397b"

SELECTION LOGIC:
- If complexity is 'hard' OR needs integrated tool planning -> MUST use "deepseek_v3.2".
- If complexity is 'medium' AND needs logical reasoning -> Use "deepseek_v3.1" or "qwq_32b".
- If style is 'naval_analyst' and needs high quality -> Use "nemotron_super" or "deepseek_v3.1".

EXAMPLE OF CORRECT OUTPUT:
User: "Deep meta comparison between Sakura and Eagle Union survival stats"
Response:
{
  "intent": "meta_comparison",
  "complexity": "hard",
  "reasoning": "Deep analysis of faction-wide stats requires the 685B giant's reasoning.",
  "execution_plan": {
    "primary_tool": "sql",
    "steps": [{ "action": "query_sql", "query": "SELECT n.name, AVG(ss.stat_value) FROM ships s JOIN ship_stats ss ON s.id = ss.ship_id JOIN nations n ON s.nation_id = n.id WHERE n.name IN ('Sakura Empire', 'Eagle Union') AND ss.stat_key IN ('HP', 'Evasion') GROUP BY n.name" }],
    "thinker_model_required": "deepseek_v3.2"
  },
  "synthesizer_config": {
    "style": "naval_analyst",
    "model": "deepseek_v3.1"
  }
}

CRITICAL: Return ONLY the JSON object. Use "deepseek_v3.2" for any complex SQL or multi-step reasoning. Do NOT use markdown code blocks. NO reasoning_content.`,
						},
						{ role: 'user', content: query }
					],
				});
				return this.handleAIResponse(answer);
			}

			// TẦNG 2: THINKER (QwQ-32B) - Nhận messages từ Client
			if (role === 'think') {
				if (!messages) return new Response(JSON.stringify({ error: "Missing 'messages' for think role" }), { status: 400 });
				const answer: any = await env.AI.run('@cf/qwen/qwq-32b', { messages });
				return this.handleAIResponse(answer);
			}

			// TẦNG 3: SYNTHESIZER (Qwen-30B-FP8) - Nhận messages từ Client
			if (role === 'synthesize') {
				if (!messages) return new Response(JSON.stringify({ error: "Missing 'messages' for synthesize role" }), { status: 400 });
				const answer: any = await env.AI.run('@cf/qwen/qwen3-30b-a3b-fp8', { messages });
				return this.handleAIResponse(answer);
			}

			return new Response(JSON.stringify({ error: "Invalid role" }), { status: 400 });

		} catch (err) {
			return new Response(JSON.stringify({ error: 'Internal Error', detail: String(err) }), { status: 500 });
		}
	},

	handleAIResponse(answer: any) {
		let responseText = answer?.choices?.[0]?.message?.content || answer?.response || answer?.answer || JSON.stringify(answer);
		let parsed;
		try {
			const jsonMatch = responseText.match(/\{[\s\S]*\}/);
			parsed = JSON.parse(jsonMatch ? jsonMatch[0] : responseText);
		} catch {
			parsed = { raw: responseText };
		}
		return new Response(JSON.stringify(parsed), { headers: { 'content-type': 'application/json' } });
	}
} satisfies ExportedHandler<Env>;
