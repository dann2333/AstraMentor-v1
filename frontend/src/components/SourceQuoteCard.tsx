import { FileText } from "lucide-react";

interface SourceQuoteCardProps {
  /** 原文引用内容 */
  sourceText: string;
  /** 页码范围（如 "第 3 页"） */
  pageInfo?: string;
  /** 章节标题 */
  heading?: string;
}

/**
 * 原文引用卡片组件
 *
 * NOTE: 文档模式下展示在教学内容旁，帮助用户对照原文理解知识点
 * 视觉上使用左侧紫色边框和浅色背景与教学内容区分
 */
export function SourceQuoteCard({ sourceText, pageInfo, heading }: SourceQuoteCardProps) {
  if (!sourceText) return null;

  return (
    <div className="my-3 overflow-hidden rounded-[var(--glass-radius-md)] border border-accent/30 bg-accent/[0.06]">
      {/* 卡片标题栏 */}
      <div className="flex items-center gap-2 border-b border-accent/20 bg-accent/10 px-3 py-2">
        <FileText className="h-3.5 w-3.5 text-accent" />
        <span className="text-xs font-medium text-accent">文档原文</span>
        {pageInfo && (
          <span className="ml-auto text-xs text-accent/70">
            {pageInfo}
          </span>
        )}
      </div>

      {/* 原文内容 */}
      <div className="px-3 py-2">
        {heading && (
          <p className="mb-1 text-xs font-medium text-accent">
            {heading}
          </p>
        )}
        <p className="whitespace-pre-wrap text-xs leading-relaxed text-foreground/75">
          {sourceText.length > 500 ? sourceText.slice(0, 500) + '...' : sourceText}
        </p>
      </div>
    </div>
  );
}
