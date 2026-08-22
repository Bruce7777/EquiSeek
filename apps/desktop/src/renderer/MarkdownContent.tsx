import ReactMarkdown, { type UrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';

const safeUrlTransform: UrlTransform = (url) => {
  const normalized = url.trim();
  if (/^(https?:|mailto:)/i.test(normalized) || normalized.startsWith('#')) return normalized;
  return '';
};

export function normalizeLegacyMarkdown(source: string): string {
  const newlineCount = source.match(/\n/g)?.length || 0;
  const looksFlattened = newlineCount < 2 && /(?:^|\s)#{2,6}\s+/.test(source) && /\s(?:[-*]|>)\s+/.test(source);
  if (!looksFlattened) return source;
  return source
    .replace(/\s+(#{2,6}\s+)/g, '\n\n$1')
    .replace(/\s+>\s+/g, '\n\n> ')
    .replace(/\s+[-*]\s+(?=\S)/g, '\n- ')
    .trim();
}

export function MarkdownContent({ children, compact = false }: { children: string; compact?: boolean }) {
  return (
    <div className={`markdown-body${compact ? ' compact' : ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={safeUrlTransform}
        components={{
          a: ({ children: label, href, ...props }) => href ? (
            <a {...props} href={href} target="_blank" rel="noreferrer noopener">{label}</a>
          ) : <span>{label}</span>,
          img: () => null,
          table: ({ children }) => <div className="markdown-table-scroll"><table>{children}</table></div>,
        }}
      >
        {normalizeLegacyMarkdown(children)}
      </ReactMarkdown>
    </div>
  );
}
