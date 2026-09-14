import type { Components } from 'react-markdown';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import 'katex/dist/katex.min.css';

interface MarkdownContentProps {
  content: string;
  className?: string;
}

const components: Components = {
  ul: ({ node, ...props }) => {
    void node;
    return <ul className="list-disc pl-7 my-2 space-y-1" {...props} />;
  },
  ol: ({ node, ...props }) => {
    void node;
    return <ol className="list-decimal pl-7 my-2 space-y-1" {...props} />;
  },
  h1: ({ node, ...props }) => {
    void node;
    return <h1 className="text-xl font-bold mt-5 mb-2" {...props} />;
  },
  h2: ({ node, ...props }) => {
    void node;
    return <h2 className="text-lg font-bold mt-4 mb-2" {...props} />;
  },
  h3: ({ node, ...props }) => {
    void node;
    return <h3 className="text-base font-bold mt-3 mb-1" {...props} />;
  },
  a: ({ node, ...props }) => {
    void node;
    return <a className="text-primary underline underline-offset-4" target="_blank" rel="noopener noreferrer" {...props} />;
  },
  blockquote: ({ node, ...props }) => {
    void node;
    return <blockquote className="border-l-4 border-primary/40 pl-4 italic my-3 text-muted-foreground" {...props} />;
  },
  p: ({ node, ...props }) => {
    void node;
    return <p className="leading-7 mb-3 last:mb-0" {...props} />;
  },
  // GFM 表格。remark-gfm 一直是开着的，表格能解析成 <table>，但之前既没有
  // 组件覆写也没有 CSS —— 于是渲染成一坨没边框、列宽乱挤的原生表格，窄列还
  // 会被容器的 break-words 逐字断行，完全读不了。
  //
  // 外面套一层横向滚动：教材里的表格经常有三四列长文本，宁可让它横向滚，
  // 也不要把列压到一个字一行。
  table: ({ node, ...props }) => {
    void node;
    return (
      <div className="ai-table-scroll">
        <table className="ai-table" {...props} />
      </div>
    );
  },
  thead: ({ node, ...props }) => {
    void node;
    return <thead className="ai-table__head" {...props} />;
  },
  th: ({ node, ...props }) => {
    void node;
    return <th className="ai-table__th" {...props} />;
  },
  td: ({ node, ...props }) => {
    void node;
    return <td className="ai-table__td" {...props} />;
  },
  hr: ({ node, ...props }) => {
    void node;
    return <hr className="ai-content__rule" {...props} />;
  },
  code: ({ node, className, children, ...props }) => {
    void node;
    const match = /language-(\w+)/.exec(className || '');
    if (match) {
      return (
        <SyntaxHighlighter
          style={vscDarkPlus}
          language={match[1]}
          PreTag="div"
          // 圆角和边框交给 CSS（.ai-content pre），别在这儿写死一个只在暗色
          // 主题下成立的 #171225 —— 护眼模式下那是一道突兀的黑框。
          className="ai-code-block"
          customStyle={{ borderRadius: undefined, border: undefined, margin: undefined }}
        >
          {String(children).replace(/\n$/, '')}
        </SyntaxHighlighter>
      );
    }
    return (
      <code className={`${className ?? ''} bg-muted px-1.5 py-0.5 text-sm font-mono`} {...props}>
        {children}
      </code>
    );
  },
};

export function MarkdownContent({ content, className = '' }: MarkdownContentProps) {
  return (
    <div className={`ai-content min-w-0 break-words ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false }]]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
