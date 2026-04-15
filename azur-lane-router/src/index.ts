export default {
	async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
		if (request.method !== 'POST') {
			return new Response('Method Not Allowed', { status: 405 });
		}

		try {
			const body: any = await request.json();
			const { model, messages, text } = body;

			// 1. Handle Embedding Task
			if (text) {
				const targetModel = (model === 'bge-m3' ? '@cf/baai/bge-m3' : model || '@cf/baai/bge-m3') as any;
				const result = await env.AI.run(targetModel, { text });
				return new Response(JSON.stringify(result), { headers: { 'content-type': 'application/json' } });
			}

			// 2. Handle Chat Task
			if (!model || !messages) {
				return new Response(JSON.stringify({ error: "Missing 'model' or 'messages' field (or 'text' for embeddings)" }), { status: 400 });
			}

			// Mapping model names to Cloudflare internal IDs
			const modelMap: Record<string, string> = {
				'qwq_32b': '@cf/baai/qwq-32b',
				'qwen3_30b_fp8': '@cf/qwen/qwen3-30b-a3b-fp8',
				'glm_4.7_flash': '@cf/zai-org/glm-4.7-flash',
				'llama_3.1_8b': '@cf/meta/llama-3.1-8b-instruct',
				'deepseek_r1_distill_qwen_32b': '@cf/deepseek-ai/deepseek-r1-distill-qwen-32b',
				'bge-m3': '@cf/baai/bge-m3'
			};

			const targetModel = (modelMap[model] || model) as keyof AiModels;

			const answer: any = await env.AI.run(targetModel, { messages });
			
			// Standardizing output
			let content = answer?.choices?.[0]?.message?.content || answer?.response || answer?.answer || JSON.stringify(answer);
			
			return new Response(JSON.stringify({
				content: content,
				model: targetModel,
				provider: 'cloudflare'
			}), { headers: { 'content-type': 'application/json' } });

		} catch (err) {
			return new Response(JSON.stringify({ error: 'Internal Error', detail: String(err) }), { status: 500 });
		}
	}
} satisfies ExportedHandler<Env>;
