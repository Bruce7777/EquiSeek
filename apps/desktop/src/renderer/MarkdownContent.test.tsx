import { render, screen } from '@testing-library/react';
import { MarkdownContent } from './MarkdownContent';

describe('MarkdownContent', () => {
  it('renders CommonMark and GFM structures', () => {
    const { container } = render(<MarkdownContent>{`# 标题

**加粗**与\`代码\`

- 列表项

| 字段 | 值 |
| --- | --- |
| 动作 | 等待 |

> 风险提示`}</MarkdownContent>);

    expect(screen.getByRole('heading', { name: '标题', level: 1 })).toBeInTheDocument();
    expect(container.querySelector('strong')).toHaveTextContent('加粗');
    expect(container.querySelector('code')).toHaveTextContent('代码');
    expect(container.querySelector('table')).not.toBeNull();
    expect(container.querySelector('blockquote')).toHaveTextContent('风险提示');
  });

  it('ignores raw HTML and removes dangerous link protocols', () => {
    const { container } = render(<MarkdownContent>{`<script>window.hacked = true</script>

[危险链接](javascript:alert(1))

[安全链接](https://example.com)`}</MarkdownContent>);

    expect(container.querySelector('script')).toBeNull();
    expect(screen.queryByRole('link', { name: '危险链接' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '安全链接' })).toHaveAttribute('href', 'https://example.com');
  });

  it('restores structural breaks in flattened legacy conversation markdown', () => {
    const { container } = render(<MarkdownContent compact>{'## 当前结论：等待 ### 为什么 - 月线方向尚未转多 - 日线等待触发 > 数据来源：历史快照'}</MarkdownContent>);

    expect(screen.getByRole('heading', { name: '当前结论：等待', level: 2 })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '为什么', level: 3 })).toBeInTheDocument();
    expect(container.querySelectorAll('li')).toHaveLength(2);
    expect(container.querySelector('blockquote')).toHaveTextContent('数据来源：历史快照');
  });
});
