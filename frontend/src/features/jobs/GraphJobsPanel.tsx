import { useEffect, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, ChevronDown, Loader2, Play, X } from 'lucide-react';
import type { TrackedJob } from './useGraphJobs';

interface GraphJobsPanelProps {
  jobs: TrackedJob[];
  onOpen: (jobId: string) => void;
  onDismiss: (jobId: string) => void;
}

const STATUS_LABEL: Record<string, string> = {
  running: '生成中',
  done: '已完成',
  failed: '失败',
};

/** 事件流里每种 kind 的前缀标签。reasoning 单独标出来，它是最长的那段等待。 */
const KIND_LABEL: Record<string, string> = {
  progress: '阶段',
  reasoning: '思考',
  content: '输出',
  error: '错误',
};

function elapsed(from: string, to?: string | null): string {
  const start = new Date(from).getTime();
  const end = to ? new Date(to).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function JobCard({ job, onOpen, onDismiss }: { job: TrackedJob } & Omit<GraphJobsPanelProps, 'jobs'>) {
  // 跑着的任务默认摊开实时输出 —— 这个面板存在的意义就是让人看见它在动。
  // 失败的也摊开：原因不该藏在一次点击后面。只有已完成的默认收起。
  const [expanded, setExpanded] = useState(job.status !== 'done');
  const logRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  // 跟着新事件滚到底，但用户自己往上翻了就不要再抢走滚动位置。
  useEffect(() => {
    const el = logRef.current;
    if (!el || !expanded || !pinnedRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [job.log, expanded]);

  const onScroll = () => {
    const el = logRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };

  return (
    <article className="job-card glass glass--thin glass--grain" data-status={job.status}>
      <div className="job-card__head">
        <span className="job-card__icon" aria-hidden>
          {job.status === 'running' ? <Loader2 size={15} className="job-spin" />
            : job.status === 'done' ? <CheckCircle2 size={15} />
            : <AlertCircle size={15} />}
        </span>
        <div className="job-card__title">
          <strong title={job.title}>{job.title}</strong>
          <span>
            {STATUS_LABEL[job.status] ?? job.status} · {elapsed(job.created_at, job.finished_at)}
            {job.course_title ? ` · ${job.course_title}` : ''}
          </span>
        </div>
        <button
          type="button"
          className="job-card__toggle"
          aria-expanded={expanded}
          aria-label={expanded ? '收起实时输出' : '展开实时输出'}
          onClick={() => setExpanded((v) => !v)}
        >
          <ChevronDown size={15} style={{ transform: expanded ? 'rotate(180deg)' : undefined }} />
        </button>
        <button
          type="button"
          className="job-card__close"
          aria-label="从列表移除"
          onClick={() => onDismiss(job.job_id)}
        >
          <X size={14} />
        </button>
      </div>

      <p className="job-card__message">{job.error || job.message}</p>

      {job.status === 'running' && <div className="job-card__bar" aria-hidden><i /></div>}

      {expanded && (
        <div className="job-card__log" ref={logRef} onScroll={onScroll} role="log" aria-live="polite">
          {job.dropped_events > 0 && (
            <p className="job-log__row" data-kind="progress">
              <span>省略</span>较早的 {job.dropped_events} 条已滚出保留范围
            </p>
          )}
          {job.log.length === 0 ? (
            <p className="job-log__row" data-kind="progress"><span>等待</span>还没有输出…</p>
          ) : job.log.map((event) => (
            <p className="job-log__row" key={event.seq} data-kind={event.kind}>
              <span>{KIND_LABEL[event.kind] ?? event.kind}</span>
              {event.text}
            </p>
          ))}
        </div>
      )}

      {job.status === 'done' && (
        <button type="button" className="job-card__open" onClick={() => onOpen(job.job_id)}>
          <Play size={13} />打开这张星图
        </button>
      )}
    </article>
  );
}

/**
 * 正在生成的星图列表。
 *
 * 生成跑在服务端的任务里，所以这个面板刷新之后还在，进度也接得回去 —— 以前
 * 一刷新就什么都不剩，中途还会被反代的读超时掐成一句 network error。
 */
export function GraphJobsPanel({ jobs, onOpen, onDismiss }: GraphJobsPanelProps) {
  if (!jobs.length) return null;
  const running = jobs.filter((j) => j.status === 'running').length;

  return (
    <section className="job-panel" aria-label="星图生成任务">
      <header className="job-panel__head">
        <strong>生成任务</strong>
        <span>{running > 0 ? `${running} 个正在生成` : '都已结束'}</span>
      </header>
      <div className="job-panel__list">
        {jobs.map((job) => (
          <JobCard key={job.job_id} job={job} onOpen={onOpen} onDismiss={onDismiss} />
        ))}
      </div>
    </section>
  );
}
