import { API_BASE_URL, authorizationHeader, notifyUnauthorized } from './client';
import { ApiRequestError } from './errors';
import type { GraphData } from '../types';

export type StreamEventName =
  | 'meta'
  | 'content_delta'
  | 'reasoning_delta'
  | 'warning'
  | 'citations'
  | 'sources'
  | 'progress'
  | 'done'
  | 'error';

export interface StreamEvent {
  event: StreamEventName;
  data: Record<string, unknown>;
}

/** Read JSON SSE frames without relying on EventSource (which cannot POST). */
export async function streamLearning(
  endpoint: string,
  body: Record<string, unknown>,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      // SSE 走的是 fetch，绕开了 axios 拦截器，令牌必须在这里手动带上。
      ...authorizationHeader(),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    if (response.status === 401) {
      // SSE 绕开了 axios 拦截器，令牌失效时同样要退出登录，
      // 否则头部还显示着用户名，用户只会看到一条看不懂的报错。
      notifyUnauthorized();
    }
    const contentType = response.headers.get('content-type') || '';
    const rawPayload = await response.text();
    let payload: unknown = rawPayload;
    if (contentType.includes('application/json')) {
      try {
        payload = JSON.parse(rawPayload) as unknown;
      } catch {
        payload = rawPayload;
      }
    }
    throw new ApiRequestError(response.status, payload, `HTTP ${response.status}`);
  }
  if (!response.body) throw new Error('浏览器没有收到流式响应体');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const consumeFrame = (frame: string) => {
    let event: StreamEventName = 'content_delta';
    const dataLines: string[] = [];
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim() as StreamEventName;
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length) return;
    const data = JSON.parse(dataLines.join('\n')) as Record<string, unknown>;
    onEvent({ event, data });
    if (event === 'error') throw new Error(String(data.message || '流式请求失败'));
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() || '';
    for (const frame of frames) consumeFrame(frame);
    if (done) break;
  }
  if (buffer.trim()) consumeFrame(buffer);
}

export interface GraphProgress {
  step: string;
  message: string;
}

/**
 * 通过 SSE 生成星图并回报真实进度。
 * onProgress 收到每个阶段回调；返回完成时的完整 GraphData。
 */
export async function generateGraphStream(
  endpoint: string,
  body: Record<string, unknown>,
  onProgress: (p: GraphProgress) => void,
  signal?: AbortSignal,
): Promise<GraphData> {
  let graph: GraphData | null = null;
  let errorMessage = '';
  await streamLearning(
    endpoint,
    body,
    (ev) => {
      if (ev.event === 'progress') {
        onProgress({
          step: String(ev.data.step ?? ''),
          message: String(ev.data.message ?? ''),
        });
      } else if (ev.event === 'done') {
        graph = (ev.data.graph as GraphData) ?? null;
      } else if (ev.event === 'error') {
        errorMessage = String(ev.data.message ?? '星图生成失败');
      }
    },
    signal,
  );
  if (errorMessage) throw new Error(errorMessage);
  if (!graph) throw new Error('未收到星图数据');
  return graph;
}
