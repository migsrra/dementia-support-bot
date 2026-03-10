const INVOKE_AGENT_API_URL = import.meta.env.VITE_INVOKE_AGENT_API_URL ?? "";

export type AgentAttribution = {
	citations?: unknown[];
};

export type InvokeAgentResponse = {
	message: string;
	response: string;
	attribution?: AgentAttribution;
};

function assertConfigured(name: string, value: string) {
	if (!value) throw new Error(`${name} is not configured.`);
}

function joinUrl(base: string, path: string) {
	return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

async function fetchOrThrow(input: RequestInfo | URL, init?: RequestInit) {
	const res = await fetch(input, init);
	if (!res.ok) {
		const text = await res.text().catch(() => "");
		throw new Error(`Request failed (${res.status}): ${text || res.statusText}`);
	}
	return res;
}

export async function invokeAgent(
	sessionID: string,
	prompt: string,
	signal?: AbortSignal,
): Promise<InvokeAgentResponse> {
	assertConfigured("INVOKE_AGENT_API_URL", INVOKE_AGENT_API_URL);

	const trimmedSessionId = sessionID.trim();
	if (!trimmedSessionId) {
		throw new Error("sessionID is required.");
	}

	const url = joinUrl(INVOKE_AGENT_API_URL, encodeURIComponent(trimmedSessionId));
	const res = await fetchOrThrow(url, {
		method: "POST",
		body: prompt,
		signal,
	});

	return (await res.json()) as InvokeAgentResponse;
}

