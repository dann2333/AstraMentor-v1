import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MarkdownContent } from './MarkdownContent';

/**
 * 这几条是对着一个真实故障写的：教材里的三列表格在课程正文里被渲染成一坨
 * 挤在一起的东西 —— 没有边框、没有内边距，第一列被压到一个字一行，完全读
 * 不出行列关系。
 *
 * 原因不是 remark-gfm 没开（它一直开着，表格确实解析成了 <table>），而是
 * 既没有组件覆写也没有任何表格 CSS。所以这里钉的是"表格必须被渲染成带样式
 * 的表格结构"。
 */
describe('MarkdownContent 的 GFM 表格', () => {
  const table = [
    '| 你需要准备的 | 类比 | 教材中的对应配置 |',
    '| --- | --- | --- |',
    '| 顾问的电话号码 | 打给谁 | 完整端点 |',
    '| 你的身份凭证 | 证明你有权咨询 | API Key |',
  ].join('\n');

  it('把管道语法渲染成真正的表格结构', () => {
    render(<MarkdownContent content={table} />);

    const rendered = screen.getByRole('table');
    expect(rendered).toBeTruthy();
    // 三列表头都在，而不是被当成一段普通文字
    expect(screen.getAllByRole('columnheader').map((c) => c.textContent)).toEqual([
      '你需要准备的',
      '类比',
      '教材中的对应配置',
    ]);
    expect(screen.getAllByRole('row')).toHaveLength(3);
  });

  it('表格带着样式钩子，并且外面套了横向滚动容器', () => {
    const { container } = render(<MarkdownContent content={table} />);

    const rendered = screen.getByRole('table');
    expect(rendered.className).toContain('ai-table');
    // 少了这层，长表格只能靠压窄列宽来塞进容器，就是原来那个样子
    expect(container.querySelector('.ai-table-scroll')).toBeTruthy();
    expect(rendered.querySelectorAll('.ai-table__th').length).toBe(3);
    expect(rendered.querySelectorAll('.ai-table__td').length).toBe(6);
  });

  it('普通正文、列表、行内代码照常渲染', () => {
    render(
      <MarkdownContent
        content={'# 标题\n\n正文里有 `inline` 代码。\n\n- 第一条\n- 第二条\n'}
      />,
    );

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('标题');
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText('inline').tagName).toBe('CODE');
  });

  it('分隔线也有样式钩子', () => {
    const { container } = render(<MarkdownContent content={'上\n\n---\n\n下'} />);
    expect(container.querySelector('hr.ai-content__rule')).toBeTruthy();
  });
});
