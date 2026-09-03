/* eslint-disable @typescript-eslint/no-explicit-any -- G6's dynamic event, renderer, and extension payloads are isolated in this adapter component. */
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { CameraSetting, ExtensionCategory, Graph, register } from '@antv/g6';
import { D3Force3DLayout, Light, Line3D, ObserveCanvas3D, Sphere, ZoomCanvas3D, renderer as renderer3d } from '@antv/g6-extension-3d';
import type { GraphData } from '../../types';
import { useLanguage } from '../../contexts/LanguageContext';

// NOTE: 注册 3D 扩展组件，只需执行一次
// try-catch 防止 HMR 热更新时重复注册警告
try {
  register(ExtensionCategory.PLUGIN, '3d-light', Light);
  register(ExtensionCategory.NODE, 'sphere', Sphere);
  register(ExtensionCategory.EDGE, 'line3d', Line3D);
  register(ExtensionCategory.LAYOUT, 'd3-force-3d', D3Force3DLayout);
  register(ExtensionCategory.PLUGIN, 'camera-setting', CameraSetting);
  register(ExtensionCategory.BEHAVIOR, 'zoom-canvas-3d', ZoomCanvas3D);
  register(ExtensionCategory.BEHAVIOR, 'observe-canvas-3d', ObserveCanvas3D);
} catch { /* 已注册则忽略 */ }

interface KnowledgeGraphProps {
  data: GraphData | null;
  onNodeClick: (nodeId: string, nodeName: string, attributes: any) => void;
  onNodeContextMenu?: (event: React.MouseEvent, node: any) => void;
  theme?: 'light' | 'eye-care' | 'dark';
  onViewModeChange?: (mode: '2d' | '3d') => void;
  initialViewMode?: '2d' | '3d';
}

/**
 * 掌握度配色。
 *
 * 这里刻意不用现成的 tailwind 靛蓝/翠绿色阶 —— 那套颜色和产品其余部分
 * （珊瑚色主色、琥珀色强调色、深紫底）没有任何关系，星图一打开就像换了个
 * 软件。改成沿着自家色相走一条暖色坡道：
 *
 *   没学过（冷紫灰，混在底色里不抢眼）→ 起步（暗玫瑰）→ 学着（珊瑚 = 主色）
 *   → 熟练（橙）→ 掌握（琥珀 = 强调色）
 *
 * 色相 12 → 45 单向推进，配上亮度递增，扫一眼就知道哪几个节点还没动过；
 * 亮色主题同一条坡道压低亮度，好在米色底上站得住。
 *
 * 用十六进制而不是 CSS 变量，是因为这些值同时要喂给 3D 渲染器（three.js），
 * 它只认得能直接解析的颜色字符串。
 */
type MasteryTone = { fill: string; stroke: string; shadowColor: string; label: string };

const MASTERY_RAMP_DARK: MasteryTone[] = [
  { fill: '#686085', stroke: '#4c4763', shadowColor: 'rgba(104,96,133,0.30)', label: '#f6efe0' },
  { fill: '#ca625e', stroke: '#a44b48', shadowColor: 'rgba(202,98,94,0.30)', label: '#2a1210' },
  { fill: '#ff8161', stroke: '#d15f43', shadowColor: 'rgba(255,129,97,0.34)', label: '#3a1408' },
  { fill: '#fa9d4c', stroke: '#cd7a30', shadowColor: 'rgba(250,157,76,0.34)', label: '#3a2205' },
  { fill: '#ffd966', stroke: '#d4ac3c', shadowColor: 'rgba(255,217,102,0.38)', label: '#3a2c05' },
];

const MASTERY_RAMP_LIGHT: MasteryTone[] = [
  { fill: '#5f5878', stroke: '#494360', shadowColor: 'rgba(95,88,120,0.26)', label: '#ffffff' },
  { fill: '#c44945', stroke: '#9b3733', shadowColor: 'rgba(196,73,69,0.26)', label: '#ffffff' },
  { fill: '#d45735', stroke: '#ab4226', shadowColor: 'rgba(212,87,53,0.28)', label: '#2e0f06' },
  { fill: '#ca7021', stroke: '#a15716', shadowColor: 'rgba(202,112,33,0.28)', label: '#2e1a04' },
  { fill: '#c3911d', stroke: '#9a7112', shadowColor: 'rgba(195,145,29,0.30)', label: '#2e2204' },
];

/** 0 → 1 的权重落到 5 档坡道上。 */
const rampIndex = (weight: number): number => {
  if (weight >= 0.8) return 4;
  if (weight >= 0.6) return 3;
  if (weight >= 0.4) return 2;
  if (weight >= 0.2) return 1;
  return 0;
};

const isLightTheme = (theme?: string) => theme === 'eye-care' || theme === 'light';

const getMasteryColor = (weightA: number, theme?: string): MasteryTone =>
  (isLightTheme(theme) ? MASTERY_RAMP_LIGHT : MASTERY_RAMP_DARK)[rampIndex(weightA)];

/**
 * 连线的颜色与粗细。
 *
 * 连线只表达"关系有多强"，不该和节点抢颜色，所以统一用前景色调不同透明度：
 * 弱关系淡到几乎看不见，强关系才实起来。
 */
const EDGE_RAMP_DARK = ['rgba(250,240,214,0.14)', 'rgba(250,240,214,0.22)', 'rgba(250,240,214,0.32)', 'rgba(250,240,214,0.44)', 'rgba(250,240,214,0.58)'];
const EDGE_RAMP_LIGHT = ['rgba(62,50,40,0.16)', 'rgba(62,50,40,0.26)', 'rgba(62,50,40,0.36)', 'rgba(62,50,40,0.48)', 'rgba(62,50,40,0.62)'];
const EDGE_WIDTHS = [1.2, 2, 3, 4, 5];

const getEdgeStyle = (weight: number, theme?: string) => {
  const i = rampIndex(weight);
  return {
    stroke: (isLightTheme(theme) ? EDGE_RAMP_LIGHT : EDGE_RAMP_DARK)[i],
    lineWidth: EDGE_WIDTHS[i],
  };
};

/** 强调色，用于悬停与高亮，同样取自主题色板。 */
const ACCENT_WARM = '#ffd966';   // 琥珀：父节点 / 高亮
const ACCENT_CORAL = '#ff8161';  // 珊瑚：子节点
const DIM_DARK = '#3a3550';
const DIM_LIGHT = '#cfc6bb';

/**
 * 画布与默认节点配色。画布本身保持透明，让底下的玻璃面板和粒子透出来 ——
 * 星图铺一块不透明底色会把整页的材质切断。
 */
const getThemeColors = (theme?: string) => {
  if (isLightTheme(theme)) {
    return {
      canvasBg: 'transparent',
      defaultFill: MASTERY_RAMP_LIGHT[0].fill,
      defaultStroke: MASTERY_RAMP_LIGHT[0].stroke,
      defaultLabelFill: MASTERY_RAMP_LIGHT[0].label,
      edgeColor: EDGE_RAMP_LIGHT[1],
      shadowColor: MASTERY_RAMP_LIGHT[0].shadowColor,
    };
  }
  return {
    canvasBg: 'transparent',
    defaultFill: MASTERY_RAMP_DARK[0].fill,
    defaultStroke: MASTERY_RAMP_DARK[0].stroke,
    defaultLabelFill: MASTERY_RAMP_DARK[0].label,
    edgeColor: EDGE_RAMP_DARK[1],
    shadowColor: MASTERY_RAMP_DARK[0].shadowColor,
  };
};

/**
 * fitView 之后把缩放拉回可读区间。
 *
 * 节点标签是 16px，一旦整图被缩到 0.5 倍以下，字就只剩一团色块 —— 这正是
 * "星图看不清"的直接原因。宁可让长链条溢出视口、让人拖两下，也不要一屏塞满
 * 谁也认不出的节点。工具条上的"定位"随时能把视图拉回来。
 */
const MIN_READABLE_ZOOM = 0.7;

const fitViewReadable = (graph: Graph) => {
  try {
    const result = graph.fitView() as unknown as Promise<void> | void;
    const settle = () => {
      try {
        if (graph.getZoom() < MIN_READABLE_ZOOM) {
          graph.zoomTo(MIN_READABLE_ZOOM);
          graph.fitCenter();
        }
      } catch { /* 图已销毁 */ }
    };
    if (result && typeof (result as Promise<void>).then === 'function') {
      (result as Promise<void>).then(settle).catch(() => { /* 图已销毁 */ });
    } else {
      settle();
    }
  } catch { /* 图已销毁 */ }
};

/**
 * 对指定 Graph 实例应用节点高亮
 * NOTE: 通过修改数据中的 _dimmed 标记 + draw() 重绘来实现
 * 这种数据驱动方式不依赖 G6 的 state 系统，最可靠
 */
const applyHighlight = (graph: Graph, nodeId: string) => {
  try {
    const allEdges = graph.getEdgeData();
    const allNodes = graph.getNodeData();

    const connectedNodeIds = new Set<string>([nodeId]);
    const connectedEdgeIds = new Set<string>();

    allEdges.forEach((edge: any) => {
      if (edge.source === nodeId || edge.target === nodeId) {
        connectedEdgeIds.add(edge.id as string);
        connectedNodeIds.add(edge.source as string);
        connectedNodeIds.add(edge.target as string);
      }
    });

    // 更新每个节点的 _dimmed 标记
    allNodes.forEach((n: any) => {
      graph.updateNodeData([{
        id: n.id,
        data: { ...n.data, _dimmed: !connectedNodeIds.has(n.id as string), _selected: n.id === nodeId },
      }]);
    });
    allEdges.forEach((e: any) => {
      graph.updateEdgeData([{
        id: e.id,
        source: e.source,
        target: e.target,
        data: { ...e.data, _dimmed: !connectedEdgeIds.has(e.id as string), _highlighted: connectedEdgeIds.has(e.id as string) },
      }]);
    });

    graph.draw();
  } catch (err) {
    console.warn('[KnowledgeGraph] applyHighlight error:', err);
  }
};

/**
 * 清除高亮，移除所有 _dimmed/_selected 标记
 */
const clearHighlight = (graph: Graph) => {
  try {
    const allNodes = graph.getNodeData();
    const allEdges = graph.getEdgeData();
    allNodes.forEach((n: any) => {
      graph.updateNodeData([{
        id: n.id,
        data: { ...n.data, _dimmed: false, _selected: false },
      }]);
    });
    allEdges.forEach((e: any) => {
      graph.updateEdgeData([{
        id: e.id,
        source: e.source,
        target: e.target,
        data: { ...e.data, _dimmed: false, _highlighted: false },
      }]);
    });
    graph.draw();
  } catch (err) {
    console.warn('[KnowledgeGraph] clearHighlight error:', err);
  }
};

const KnowledgeGraph: React.FC<KnowledgeGraphProps> = ({ data, onNodeClick, onNodeContextMenu, theme, onViewModeChange, initialViewMode }) => {
  const { t } = useLanguage();
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  // NOTE: 保存当前高亮的节点 ID，在图表重渲染后恢复高亮状态
  const highlightedNodeRef = useRef<string | null>(null);
  // NOTE: 3D 模式中追踪当前 hover 的节点 ID
  // 因为 G6 3D 渲染器不支持 node:click 事件，改用 hover+原生DOM点击
  const hoveredNodeIdRef = useRef<string | null>(null);
  // NOTE: 3D 模式下节点名称标签的 HTML 覆盖层容器
  const labelsOverlayRef = useRef<HTMLDivElement>(null);
  const labelsRafRef = useRef<number>(0);
  const [layoutType, setLayoutType] = useState<'TB' | 'LR'>('TB');
  // NOTE: 2D/3D 视图模式切换
  const [viewMode, setViewMode] = useState<'2d' | '3d'>(initialViewMode || '2d');
  // NOTE: 3D 模式下默认关闭连线
  const [show3DEdges, setShow3DEdges] = useState(false);
  const [selectedEdgeInfo, setSelectedEdgeInfo] = useState<{
    id: string;
    label: string;
    weight: number;
    x: number;
    y: number;
  } | null>(null);
  // NOTE: 3D 模式下 hover 节点的 tooltip 信息（含父子关系）
  const [, setHoveredNode] = useState<{
    name: string;
    x: number;
    y: number;
    parents: string[];
    children: string[];
  } | null>(null);

  // NOTE: 3D 模式下点击节点后的信息面板数据
  const [selectedNodeInfo, setSelectedNodeInfo] = useState<{
    name: string;
    mastery: number;
    parents: { name: string; relation?: string }[];
    children: { name: string; relation?: string }[];
    attrs: Record<string, any>;
  } | null>(null);

  // NOTE: 保存回调引用，避免 Graph 事件处理闭包捕获旧值
  const onNodeClickRef = useRef(onNodeClick);
  const onNodeContextMenuRef = useRef(onNodeContextMenu);
  useEffect(() => { onNodeClickRef.current = onNodeClick; }, [onNodeClick]);
  useEffect(() => { onNodeContextMenuRef.current = onNodeContextMenu; }, [onNodeContextMenu]);

  /**
   * 将 GraphData 转换为 G6 需要的数据格式
   * 同时计算每个节点和边的视觉样式参数
   */
  const transformData = useCallback((graphData: GraphData) => {
    // NOTE: 所有业务属性放入 _attrs，样式信息用下划线前缀
    // 避免 dagre 布局算法误读 weight 等字段
      const nodes = graphData.nodes.map((n) => {
      const weightA = n.attributes?.weight_A ?? 0;
      const mastery = getMasteryColor(weightA, theme);

      return {
          id: n.id,
          data: {
            label: n.name,
            _fill: mastery.fill,
            _stroke: mastery.stroke,
            _shadowColor: mastery.shadowColor,
            _labelFill: mastery.label,
            _weightA: weightA,
            _attrs: n.attributes || {},
          },
        };
      });

      const edges = graphData.links.map((e, index) => {
        const w = e.weight || 0.1;
        const edgeStyle = getEdgeStyle(w, theme);
      return {
        id: `edge-${index}`,
        source: e.source,
        target: e.target,
        data: {
          _weight: w,
          _reason: e.reason,
          _stroke: edgeStyle.stroke,
          _lineWidth: edgeStyle.lineWidth,
        },
      };
    });

    return { nodes, edges };
  }, [theme]);

  /**
   * 布局配置：大间距 + 控制点让图谱清晰通透
   */
  const getLayoutConfig = useCallback((type: 'TB' | 'LR') => ({
    type: 'antv-dagre' as const,
    rankdir: type,
    nodeSize: [188, 46] as [number, number],
    // 间距收窄了一截。原来的 120/100 会把十来个节点的链条拉到视口的两倍高，
    // fitView 只好把整张图缩到 0.4 倍——节点上的字直接糊掉。
    nodesep: type === 'TB' ? 48 : 72,
    ranksep: type === 'TB' ? 64 : 96,
    controlPoints: true,
  }), []);

  // NOTE: 主要的 Graph 初始化与更新 effect
  // viewMode 变化时始终销毁旧实例重建，避免 2D/3D 渲染器冲突
  useEffect(() => {
    if (!containerRef.current || !data) {
      if (graphRef.current) {
        const g = graphRef.current;
        graphRef.current = null;
        g.destroy();
      }
      return;
    }

    const g6Data = transformData(data);
    const themeColors = getThemeColors(theme);

    // NOTE: viewMode 变化时必须销毁重建（2D/3D 用不同渲染器）
    // 只有同模式下的 data/theme 变化才走增量更新路径
    if (graphRef.current) {
      // 如果是同一模式的数据更新，走增量更新
      const existingGraph = graphRef.current;
      existingGraph.setData(g6Data);
      if (viewMode === '2d') {
        existingGraph.setLayout(getLayoutConfig(layoutType));
      }
      existingGraph.render().then(() => {
        if (graphRef.current === existingGraph) {
          if (viewMode === '2d') {
            fitViewReadable(existingGraph);
            if (highlightedNodeRef.current) {
              applyHighlight(existingGraph, highlightedNodeRef.current);
            }
          }
        }
      }).catch(() => { /* 忽略已销毁图表的错误 */ });
      return;
    }

    let mounted = true;
    let retryTimer: ReturnType<typeof setTimeout>;
    let graphInstance: Graph | null = null;

    const createGraph = () => {
      if (!mounted || !containerRef.current) return;

      const rect = containerRef.current.getBoundingClientRect();
      // NOTE: 容器尺寸太小时重试，面板可能还在过渡动画中
      if (rect.width < 50 || rect.height < 50) {
        retryTimer = setTimeout(createGraph, 150);
        return;
      }

      // NOTE: 根据 viewMode 分支创建不同类型的图表
      const graph = viewMode === '3d'
        ? create3DGraph(rect, g6Data)
        : create2DGraph(rect, g6Data, themeColors);

      graphInstance = graph;

      // 节点点击事件：触发学习流程
      // NOTE: 3D 模式下 node:click 不触发（WebGL 渲染器限制），改用 DOM 原生事件
      graph.on('node:click', (evt: any) => {
        const nodeId = evt.target?.id;
        if (!nodeId) return;
        const nodeData = graph.getNodeData(nodeId);
        if (nodeData) {
          if (viewMode === '2d') {
            highlightedNodeRef.current = nodeId;
            applyHighlight(graph, nodeId);
          }
          const d = nodeData.data as any;

          // NOTE: 收集父子节点信息，填充底部信息面板（2D/3D 通用）
          const allEdges = graph.getEdgeData();
          const parents: { name: string; relation?: string }[] = [];
          const children: { name: string; relation?: string }[] = [];

          allEdges.forEach((edge: any) => {
            if (edge.target === nodeId) {
              const parentData = graph.getNodeData(edge.source as string);
              parents.push({
                name: (parentData?.data as any)?.label || (edge.source as string),
                relation: edge.data?._reason,
              });
            } else if (edge.source === nodeId) {
              const childData = graph.getNodeData(edge.target as string);
              children.push({
                name: (childData?.data as any)?.label || (edge.target as string),
                relation: edge.data?._reason,
              });
            }
          });

          setSelectedNodeInfo({
            name: d?.label || nodeId,
            mastery: d?._weightA ?? 0,
            parents,
            children,
            attrs: d?._attrs || {},
          });

          onNodeClickRef.current(
            nodeId,
            d?.label || nodeId,
            d?._attrs || {}
          );
        }
      });

      // 节点右键事件：弹出详情弹窗
      graph.on('node:contextmenu', (evt: any) => {
        const nodeData = graph.getNodeData(evt.target.id);
        if (nodeData && onNodeContextMenuRef.current) {
          const syntheticEvent = {
            preventDefault: () => {},
            stopPropagation: () => {},
            clientX: evt.client?.x || 0,
            clientY: evt.client?.y || 0,
          } as unknown as React.MouseEvent;

          const d = nodeData.data as any;
          const compatNode = {
            id: nodeData.id,
            data: {
              label: d?.label || nodeData.id,
              ...(d?._attrs || {}),
            },
          };
          onNodeContextMenuRef.current(syntheticEvent, compatNode);
        }
      });

      // 边点击事件：显示关系详情弹窗
      graph.on('edge:click', (evt: any) => {
        const edgeData = graph.getEdgeData(evt.target.id);
        if (edgeData) {
          const d = edgeData.data as any;
          setSelectedEdgeInfo({
            id: edgeData.id as string,
            label: d?._reason || '',
            weight: d?._weight || 0,
            x: evt.client?.x || 0,
            y: evt.client?.y || 0,
          });
        }
      });

      // 点击画布空白区域：重置高亮 + 关闭弹窗
      graph.on('canvas:click', () => {
        if (viewMode === '2d') {
          highlightedNodeRef.current = null;
          clearHighlight(graph);
        }
        setSelectedNodeInfo(null);
        setSelectedEdgeInfo(null);
        setHoveredNode(null);
      });

      // NOTE: 3D 模式下 G6 的 node: 事件全部不触发（WebGL 渲染器限制）
      // hover/click/contextmenu 全部通过下方的 DOM 原生事件 useEffect 实现

      // NOTE: 立即设置 graphRef，让3D标签的 RAF 循环能尽早拿到实例并开始投影
      // 不再等 render() 完成，减少标签出现的延迟
      if (mounted) {
        graphRef.current = graph;
      }

      graph.render().then(() => {
        if (mounted && graphRef.current === graph) {
          // 初始渲染后立即 fitView，确保图谱居中适配视口
          if (viewMode === '2d') {
            fitViewReadable(graph);
          }
        }
      }).catch(() => { /* 忽略已销毁图表的错误 */ });
    };

    /**
     * 创建 2D DAG 图表
     */
    const create2DGraph = (rect: DOMRect, g6d: any, tc: any) => {
      return new Graph({
        container: containerRef.current!,
        width: rect.width,
        height: rect.height,
        autoFit: 'view',
        padding: [40, 40, 40, 40],
        data: g6d,
        layout: getLayoutConfig(layoutType),
        edge: {
          type: 'cubic-vertical',
          style: {
            stroke: (d: any) => {
              if (d.data?._highlighted) return ACCENT_WARM;
              if (d.data?._dimmed) return isLightTheme(theme) ? DIM_LIGHT : DIM_DARK;
              return d.data?._stroke || tc.edgeColor;
            },
            lineWidth: (d: any) => {
              if (d.data?._highlighted) return 3;
              if (d.data?._dimmed) return 0.8;
              return d.data?._lineWidth || 1.5;
            },
            opacity: (d: any) => d.data?._dimmed ? 0.2 : 1,
            endArrow: true,
            endArrowSize: (d: any) => {
              const baseWidth = d.data?._lineWidth || 1.5;
              if (d.data?._highlighted) return Math.max(14, baseWidth * 3.5);
              if (d.data?._dimmed) return 8;
              return Math.max(12, baseWidth * 3);
            },
            endArrowFill: (d: any) => {
              if (d.data?._highlighted) return ACCENT_WARM;
              if (d.data?._dimmed) return isLightTheme(theme) ? DIM_LIGHT : DIM_DARK;
              return d.data?._stroke || tc.edgeColor;
            },
            cursor: 'pointer',
          },
        },
        node: {
          type: 'rect',
          style: {
            size: [200, 48],
            radius: 12,
            labelText: (d: any) => d.data?.label || d.id || '',
            labelPlacement: 'center',
            labelFontSize: 16,
            labelFontWeight: 600,
            labelFill: (d: any) => d.data?._labelFill || tc.defaultLabelFill,
            labelFontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
            labelWordWrap: true,
            labelWordWrapWidth: 172,
            labelMaxLines: 2,
            fill: (d: any) => d.data?._fill || tc.defaultFill,
            stroke: (d: any) => {
              if (d.data?._selected) return isLightTheme(theme) ? '#2e2118' : '#fdf6e3';
              return d.data?._stroke || tc.defaultStroke;
            },
            lineWidth: (d: any) => {
              return d.data?._selected ? 3 : 1.5;
            },
            opacity: (d: any) => d.data?._dimmed ? 0.2 : 1,
            shadowColor: (d: any) => d.data?._shadowColor || tc.shadowColor,
            shadowBlur: (d: any) => d.data?._selected ? 24 : 12,
            shadowOffsetX: 0,
            shadowOffsetY: 4,
            cursor: 'pointer',
          },
        },
        behaviors: ['drag-element', 'drag-canvas', 'zoom-canvas'],
      });
    };

    /**
     * 创建 3D 力导向图表
     * NOTE: Sphere 节点不支持 labelText，通过 hover tooltip 显示名称
     */
    const create3DGraph = (rect: DOMRect, g6d: any) => {
      // NOTE: 统计每个节点的连接数，用于动态调整半径
      const degreeMap: Record<string, number> = {};
      g6d.edges.forEach((e: any) => {
        degreeMap[e.source] = (degreeMap[e.source] || 0) + 1;
        degreeMap[e.target] = (degreeMap[e.target] || 0) + 1;
      });

      // NOTE: 为节点注入随机 3D 初始坐标，充分分散在球形空间中
      const data3d = {
        ...g6d,
        nodes: g6d.nodes.map((n: any) => ({
          ...n,
          style: {
            ...n.style,
            x: (Math.random() - 0.5) * 500,
            y: (Math.random() - 0.5) * 500,
            z: (Math.random() - 0.5) * 500,
          },
        })),
      };

      return new Graph({
        container: containerRef.current!,
        renderer: renderer3d,
        width: rect.width,
        height: rect.height,
        data: data3d,
        layout: {
          type: 'd3-force-3d',
          // NOTE: 大间距 + 强斥力，让节点充分散开，减少连线交叉
          link: { distance: 300 },
          charge: { strength: -800 },
          center: { x: rect.width / 2, y: rect.height / 2, z: 0 },
          simulation: {
            alphaDecay: 0.008,
            velocityDecay: 0.3,
          },
        },
        node: {
          type: 'sphere',
          style: {
            materialType: 'phong',
            // NOTE: 3D 节点颜色：强制使用星空主题的高亮发光感
            fill: (d: any) => {
              if (d.data?._dimmed) return DIM_DARK;
              return getMasteryColor(d.data?._weightA || 0, 'dark').fill;
            },
            // NOTE: 根据连接数动态调整半径，hub 节点更大更醒目
            radius: (d: any) => {
              const degree = degreeMap[d.id] || 1;
              return Math.min(8 + degree * 3, 20);
            },
          },
        },
        edge: {
          type: 'line3d',
          style: {
            // NOTE: 3D 边样式：强制使用星光主题连线
            stroke: (d: any) => {
              if (d.data?._hoverParent) return ACCENT_WARM;
              if (d.data?._hoverChild) return ACCENT_CORAL;
              if (d.data?._highlighted) return ACCENT_WARM;
              return getEdgeStyle(d.data?._weight || 0.1, 'dark').stroke;
            },
            lineWidth: (d: any) => {
              if (d.data?._hoverParent || d.data?._hoverChild) return 2.5;
              if (d.data?._highlighted) return 2;
              return 1;
            },
            opacity: (d: any) => {
              if (d.data?._hoverParent || d.data?._hoverChild) return 0.9;
              if (d.data?._highlighted) return 0.8;
              return d.data?._hiddenEdges ? 0 : (theme === 'light' ? 0.3 : 0.2); 
            },
          },
        },
        behaviors: [
          {
            // NOTE: 设置 trigger，让左键拖拽空白区域旋转相机
            // 不设置 trigger 时 observe-canvas-3d 会拦截所有 drag，导致节点 click 失效
            type: 'observe-canvas-3d',
          },
          'zoom-canvas-3d',
        ],
        plugins: [
          {
            type: '3d-light',
            directional: {
              direction: [0, 0, 1],
              intensity: 0.8,
            },
            ambient: {
              // NOTE: 增加环境光亮度，让球体更清晰立体，模拟星光环境
              intensity: 0.8,
            },
          },
        ],
      });
    };

    createGraph();

    return () => {
      mounted = false;
      clearTimeout(retryTimer);
      // NOTE: 先清 ref 再销毁，避免异步回调访问已销毁实例
      graphRef.current = null;
      if (graphInstance) {
        try { graphInstance.destroy(); } catch { /* 可能已被手动销毁 */ }
      }
    };
  }, [data, theme, viewMode]); // eslint-disable-line react-hooks/exhaustive-deps

  // NOTE: 监听 3D 模式连线可见性切换，更新边数据并触发重绘
  useEffect(() => {
    if (viewMode === '3d' && graphRef.current) {
      const graph = graphRef.current;
      try {
        const allEdges = graph.getEdgeData();
        allEdges.forEach((e: any) => {
          graph.updateEdgeData([{
            id: e.id,
            source: e.source,
            target: e.target,
            data: { ...e.data, _hiddenEdges: !show3DEdges },
          }]);
        });
        graph.draw();
      } catch { /* 忽略更新错误 */ }
    }
  }, [show3DEdges, viewMode]);

  // NOTE: 单独的 ResizeObserver，面板重新打开时自动 resize + fitView
  useEffect(() => {
    if (!containerRef.current) return;

    let fitViewTimer: ReturnType<typeof setTimeout>;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0 && graphRef.current) {
          graphRef.current.resize(width, height);
          // NOTE: 3D 模式下只 resize 不 fitView，避免异常放大
          // 2D 模式防抖 fitView，确保图谱居中适配
          if (viewMode === '2d') {
            clearTimeout(fitViewTimer);
            fitViewTimer = setTimeout(() => {
              if (graphRef.current) fitViewReadable(graphRef.current);
            }, 200);
          }
        }
      }
    });

    observer.observe(containerRef.current);
    return () => {
      clearTimeout(fitViewTimer);
      observer.disconnect();
    };
  }, [viewMode]);

  // NOTE: 3D 模式专用——手动节点拾取（G6 3D 渲染器不触发任何节点事件）
  // 通过遍历所有节点、投影到屏幕坐标，与鼠标距离对比来实现 hover/click/contextmenu
  useEffect(() => {
    if (viewMode !== '3d' || !containerRef.current) return;

    const container = containerRef.current;

    /**
     * 将 3D 世界坐标通过 VP 矩阵投影到 2D 屏幕坐标
     * @param viewMatrix camera.getViewTransform() 返回的 mat4
     * @param projMatrix camera.getPerspective() 返回的 mat4
     * @param worldPos [x, y, z] 世界坐标
     * @param width 画布宽度（像素）
     * @param height 画布高度（像素）
     * @returns [screenX, screenY] 或 null
     */
    const projectToScreen = (
      viewMatrix: number[] | Float32Array,
      projMatrix: number[] | Float32Array,
      worldPos: number[],
      width: number,
      height: number
    ): [number, number] | null => {
      const [wx, wy, wz] = worldPos;

      // 1. 应用 View 矩阵：viewPos = viewMatrix * worldPos
      const vx = viewMatrix[0] * wx + viewMatrix[4] * wy + viewMatrix[8] * wz + viewMatrix[12];
      const vy = viewMatrix[1] * wx + viewMatrix[5] * wy + viewMatrix[9] * wz + viewMatrix[13];
      const vz = viewMatrix[2] * wx + viewMatrix[6] * wy + viewMatrix[10] * wz + viewMatrix[14];
      const vw = viewMatrix[3] * wx + viewMatrix[7] * wy + viewMatrix[11] * wz + viewMatrix[15];

      // 2. 应用 Projection 矩阵：clipPos = projMatrix * viewPos
      const cx = projMatrix[0] * vx + projMatrix[4] * vy + projMatrix[8] * vz + projMatrix[12] * vw;
      const cy = projMatrix[1] * vx + projMatrix[5] * vy + projMatrix[9] * vz + projMatrix[13] * vw;
      const cw = projMatrix[3] * vx + projMatrix[7] * vy + projMatrix[11] * vz + projMatrix[15] * vw;

      // 3. 透视除法
      if (Math.abs(cw) < 1e-6) return null;
      const ndcX = cx / cw;
      const ndcY = cy / cw;

      // 4. NDC [-1,1] → 屏幕像素
      const screenX = ((ndcX + 1) / 2) * width;
      const screenY = ((1 - ndcY) / 2) * height;

      return [screenX, screenY];
    };

    /**
     * 在 3D scene 中查找鼠标位置最近的节点
     * @returns 命中的节点 ID + 其屏幕坐标（viewport-relative，可直接用于 position:fixed）
     */
    const findNodeAtPosition = (clientX: number, clientY: number): { id: string; sx: number; sy: number } | null => {
      const graph = graphRef.current;
      if (!graph) return null;

      const rect = container.getBoundingClientRect();
      const mouseX = clientX - rect.left;
      const mouseY = clientY - rect.top;

      const canvas = (graph as any).context?.canvas;
      const camera = canvas?.getCamera?.();
      if (!camera) return null;

      let viewMatrix: any;
      let projMatrix: any;
      try {
        viewMatrix = camera.getViewTransform();
        projMatrix = camera.getPerspective();
      } catch {
        return null;
      }

      if (!viewMatrix || !projMatrix) return null;

      const allNodes = graph.getNodeData();
      let nearest: { id: string; sx: number; sy: number } | null = null;
      let minDist = Infinity;
      const hitRadius = 30;

      for (const node of allNodes) {
        let pos: number[];
        try {
          pos = graph.getElementPosition(node.id as string) as unknown as number[];
        } catch {
          continue;
        }
        if (!pos || pos.length < 3) continue;

        const screen = projectToScreen(viewMatrix, projMatrix, pos, rect.width, rect.height);
        if (!screen) continue;

        const dx = screen[0] - mouseX;
        const dy = screen[1] - mouseY;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < hitRadius && dist < minDist) {
          minDist = dist;
          // NOTE: screen 是 container-relative，加 rect.left/top 转为 viewport-relative
          nearest = { id: node.id as string, sx: rect.left + screen[0], sy: rect.top + screen[1] };
        }
      }

      return nearest;
    };

    // NOTE: 上一次 hover 高亮的节点 ID，用于离开时清除边高亮
    let lastHoveredId: string | null = null;

    const clearHoverEdges = (graph: Graph) => {
      try {
        const allEdges = graph.getEdgeData();
        allEdges.forEach((e: any) => {
          if (e.data?._hoverParent || e.data?._hoverChild) {
            graph.updateEdgeData([{
              id: e.id,
              source: e.source,
              target: e.target,
              data: { ...e.data, _hoverParent: false, _hoverChild: false },
            }]);
          }
        });
        graph.draw();
      } catch { /* 忽略 */ }
    };

    const handleMouseMove = (e: MouseEvent) => {
      const hit = findNodeAtPosition(e.clientX, e.clientY);
      const nodeId = hit?.id || null;
      hoveredNodeIdRef.current = nodeId;
      const graph = graphRef.current;

      if (nodeId && graph && hit) {
        // 如果是同一个节点，不更新 tooltip
        if (nodeId === lastHoveredId) {
          container.style.cursor = 'pointer';
          return;
        }

        // 切换了 hover 节点 —— 先清除旧高亮
        if (lastHoveredId) clearHoverEdges(graph);
        lastHoveredId = nodeId;

        const nodeData = graph.getNodeData(nodeId);
        if (nodeData) {
          const d = nodeData.data as any;
          const allEdges = graph.getEdgeData();
          const parentNames: string[] = [];
          const childNames: string[] = [];

          allEdges.forEach((edge: any) => {
            if (edge.target === nodeId) {
              const parentData = graph.getNodeData(edge.source as string);
              parentNames.push((parentData?.data as any)?.label || edge.source);
              graph.updateEdgeData([{
                id: edge.id, source: edge.source, target: edge.target,
                data: { ...edge.data, _hoverParent: true, _hoverChild: false },
              }]);
            } else if (edge.source === nodeId) {
              const childData = graph.getNodeData(edge.target as string);
              childNames.push((childData?.data as any)?.label || edge.target);
              graph.updateEdgeData([{
                id: edge.id, source: edge.source, target: edge.target,
                data: { ...edge.data, _hoverParent: false, _hoverChild: true },
              }]);
            }
          });

          graph.draw();

          // NOTE: 用 findNodeAtPosition 返回的投影坐标定位 tooltip
          // 这与命中检测用的是同一套投影逻辑，保证位置精确
          setHoveredNode({
            name: d?.label || nodeId,
            x: hit.sx,
            y: hit.sy,
            parents: parentNames,
            children: childNames,
          });
        }
      } else {
        if (lastHoveredId && graph) {
          clearHoverEdges(graph);
          lastHoveredId = null;
        }
        setHoveredNode(null);
      }

      container.style.cursor = nodeId ? 'pointer' : 'default';
    };

    const handleClick = (_e: MouseEvent) => {
      const graph = graphRef.current;
      if (!graph) return;

      const nodeId = hoveredNodeIdRef.current;

      if (nodeId) {
        // NOTE: 点击节点—高亮关联边和节点（类似 2D 行为）
        highlightedNodeRef.current = nodeId;
        applyHighlight(graph, nodeId);

        const nodeData = graph.getNodeData(nodeId);
        if (nodeData) {
          const d = nodeData.data as any;

          // NOTE: 收集父子节点信息（含边的 reason）填充信息面板
          const allEdges = graph.getEdgeData();
          const parents: { name: string; relation?: string }[] = [];
          const children: { name: string; relation?: string }[] = [];

          allEdges.forEach((edge: any) => {
            if (edge.target === nodeId) {
              const parentData = graph.getNodeData(edge.source as string);
              parents.push({
                name: (parentData?.data as any)?.label || (edge.source as string),
                relation: edge.data?._reason,
              });
            } else if (edge.source === nodeId) {
              const childData = graph.getNodeData(edge.target as string);
              children.push({
                name: (childData?.data as any)?.label || (edge.target as string),
                relation: edge.data?._reason,
              });
            }
          });

          setSelectedNodeInfo({
            name: d?.label || nodeId,
            mastery: d?._weightA ?? 0,
            parents,
            children,
            attrs: d?._attrs || {},
          });

          // 同时触发学习流程
          onNodeClickRef.current(
            nodeId,
            d?.label || nodeId,
            d?._attrs || {}
          );
        }
      } else {
        // NOTE: 未命中节点——尝试检测是否点击了边
        const rect = container.getBoundingClientRect();
        const mouseX = _e.clientX - rect.left;
        const mouseY = _e.clientY - rect.top;

        const canvas = (graph as any).context?.canvas;
        const camera = canvas?.getCamera?.();

        let edgeHit = false;

        if (camera) {
          let viewMatrix: any;
          let projMatrix: any;
          try {
            viewMatrix = camera.getViewTransform();
            projMatrix = camera.getPerspective();
          } catch { /* 忽略 */ }

          if (viewMatrix && projMatrix) {
            const allEdges = graph.getEdgeData();
            const EDGE_HIT_THRESHOLD = 12; // 距离阈值（像素）
            let nearestEdgeDist = Infinity;
            let nearestEdge: any = null;

            for (const edge of allEdges) {
              // 获取两端节点的 3D 坐标并投影到屏幕
              let srcPos: number[], tgtPos: number[];
              try {
                srcPos = graph.getElementPosition(edge.source as string) as unknown as number[];
                tgtPos = graph.getElementPosition(edge.target as string) as unknown as number[];
              } catch { continue; }
              if (!srcPos || srcPos.length < 3 || !tgtPos || tgtPos.length < 3) continue;

              const screenSrc = projectToScreen(viewMatrix, projMatrix, srcPos, rect.width, rect.height);
              const screenTgt = projectToScreen(viewMatrix, projMatrix, tgtPos, rect.width, rect.height);
              if (!screenSrc || !screenTgt) continue;

              /**
               * 计算点到线段的最短距离
               * NOTE: 使用向量投影公式，clamp 到 [0,1] 保证在线段范围内
               */
              const dx = screenTgt[0] - screenSrc[0];
              const dy = screenTgt[1] - screenSrc[1];
              const lenSq = dx * dx + dy * dy;
              if (lenSq < 1) continue; // 退化为点，跳过

              const t = Math.max(0, Math.min(1,
                ((mouseX - screenSrc[0]) * dx + (mouseY - screenSrc[1]) * dy) / lenSq
              ));
              const projX = screenSrc[0] + t * dx;
              const projY = screenSrc[1] + t * dy;
              const dist = Math.sqrt((mouseX - projX) ** 2 + (mouseY - projY) ** 2);

              if (dist < EDGE_HIT_THRESHOLD && dist < nearestEdgeDist) {
                nearestEdgeDist = dist;
                nearestEdge = edge;
              }
            }

            if (nearestEdge) {
              edgeHit = true;
              const d = nearestEdge.data as any;
              setSelectedEdgeInfo({
                id: nearestEdge.id as string,
                label: d?._reason || '',
                weight: d?._weight || 0,
                x: _e.clientX,
                y: _e.clientY,
              });
            }
          }
        }

        if (!edgeHit) {
          // NOTE: 点击空白区域—清除高亮和信息面板
          if (highlightedNodeRef.current) {
            highlightedNodeRef.current = null;
            clearHighlight(graph);
          }
          setSelectedNodeInfo(null);
          setSelectedEdgeInfo(null);
          setHoveredNode(null);
        }
      }
    };

    const handleContextMenu = (e: MouseEvent) => {
      const nodeId = hoveredNodeIdRef.current;
      if (!nodeId || !graphRef.current || !onNodeContextMenuRef.current) return;

      e.preventDefault();
      e.stopPropagation();

      const nodeData = graphRef.current.getNodeData(nodeId);
      if (nodeData) {
        const d = nodeData.data as any;
        const syntheticEvent = {
          preventDefault: () => {},
          stopPropagation: () => {},
          clientX: e.clientX,
          clientY: e.clientY,
        } as unknown as React.MouseEvent;

        const compatNode = {
          id: nodeData.id,
          data: {
            label: d?.label || nodeData.id,
            ...(d?._attrs || {}),
          },
        };
        onNodeContextMenuRef.current(syntheticEvent, compatNode);
      }
    };

    // NOTE: 记录 mousedown 位置，用于区分「拖拽旋转」和「真正点击」
    // 拖拽旋转时鼠标会移动较大距离，不应触发清除面板的逻辑
    let mouseDownPos: { x: number; y: number } | null = null;
    const DRAG_THRESHOLD = 5; // 超过 5px 视为拖拽

    const handleMouseDown = (e: MouseEvent) => {
      mouseDownPos = { x: e.clientX, y: e.clientY };
    };

    const wrappedHandleClick = (e: MouseEvent) => {
      // NOTE: 如果鼠标在 mousedown 到 mouseup 之间移动超过阈值，视为拖拽操作，跳过点击处理
      if (mouseDownPos) {
        const dx = e.clientX - mouseDownPos.x;
        const dy = e.clientY - mouseDownPos.y;
        if (Math.sqrt(dx * dx + dy * dy) > DRAG_THRESHOLD) {
          mouseDownPos = null;
          return;
        }
      }
      mouseDownPos = null;
      handleClick(e);
    };

    container.addEventListener('mousedown', handleMouseDown);
    container.addEventListener('mousemove', handleMouseMove);
    container.addEventListener('click', wrappedHandleClick);
    container.addEventListener('contextmenu', handleContextMenu);

    return () => {
      container.removeEventListener('mousedown', handleMouseDown);
      container.removeEventListener('mousemove', handleMouseMove);
      container.removeEventListener('click', wrappedHandleClick);
      container.removeEventListener('contextmenu', handleContextMenu);
      container.style.cursor = 'default';
    };
  }, [viewMode]);

  // NOTE: 3D 模式下持续更新节点名称标签位置
  // 使用 requestAnimationFrame 将每个节点的3D坐标投影到屏幕坐标
  // 然后通过 DOM 操作更新标签位置，避免 React 渲染开销
  useEffect(() => {
    if (viewMode !== '3d' || !containerRef.current || !labelsOverlayRef.current) return;

    const overlay = labelsOverlayRef.current;
    // 缓存已创建的标签 DOM 元素，避免每帧重建
    const labelElements = new Map<string, HTMLDivElement>();

    const updateLabels = () => {
      const graph = graphRef.current;
      const container = containerRef.current;
      if (!graph || !container) {
        labelsRafRef.current = requestAnimationFrame(updateLabels);
        return;
      }

      const rect = container.getBoundingClientRect();
      const canvas = (graph as any).context?.canvas;
      const camera = canvas?.getCamera?.();

      if (!camera) {
        labelsRafRef.current = requestAnimationFrame(updateLabels);
        return;
      }

      let viewMatrix: any;
      let projMatrix: any;
      try {
        viewMatrix = camera.getViewTransform();
        projMatrix = camera.getPerspective();
      } catch {
        labelsRafRef.current = requestAnimationFrame(updateLabels);
        return;
      }

      if (!viewMatrix || !projMatrix) {
        labelsRafRef.current = requestAnimationFrame(updateLabels);
        return;
      }

      const allNodes = graph.getNodeData();
      const activeIds = new Set<string>();

      for (const node of allNodes) {
        const nodeId = node.id as string;
        activeIds.add(nodeId);

        let pos: number[];
        try {
          pos = graph.getElementPosition(nodeId) as unknown as number[];
        } catch {
          continue;
        }
        if (!pos || pos.length < 3) continue;

        // 将3D坐标投影到2D屏幕
        const [wx, wy, wz] = pos;
        const vx = viewMatrix[0] * wx + viewMatrix[4] * wy + viewMatrix[8] * wz + viewMatrix[12];
        const vy = viewMatrix[1] * wx + viewMatrix[5] * wy + viewMatrix[9] * wz + viewMatrix[13];
        const vz = viewMatrix[2] * wx + viewMatrix[6] * wy + viewMatrix[10] * wz + viewMatrix[14];
        const vw = viewMatrix[3] * wx + viewMatrix[7] * wy + viewMatrix[11] * wz + viewMatrix[15];

        const cx = projMatrix[0] * vx + projMatrix[4] * vy + projMatrix[8] * vz + projMatrix[12] * vw;
        const cy = projMatrix[1] * vx + projMatrix[5] * vy + projMatrix[9] * vz + projMatrix[13] * vw;
        const cw = projMatrix[3] * vx + projMatrix[7] * vy + projMatrix[11] * vz + projMatrix[15] * vw;

        if (Math.abs(cw) < 1e-6) continue;
        const ndcX = cx / cw;
        const ndcY = cy / cw;

        const screenX = ((ndcX + 1) / 2) * rect.width;
        const screenY = ((1 - ndcY) / 2) * rect.height;

        // 获取或创建标签元素
        let labelEl = labelElements.get(nodeId);
        if (!labelEl) {
          labelEl = document.createElement('div');
          labelEl.style.position = 'absolute';
          labelEl.style.pointerEvents = 'none';
          labelEl.style.whiteSpace = 'nowrap';
          labelEl.style.fontSize = '12px';
          labelEl.style.fontWeight = '600';
          labelEl.style.fontFamily = '"System-ui", "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif';
          labelEl.style.color = '#fdf6e3';
          labelEl.style.textShadow = '0 1px 3px rgba(0,0,0,0.95), 0 0 10px rgba(0,0,0,0.7)';
          labelEl.style.transform = 'translate(-50%, 0)';
          labelEl.style.transition = 'opacity 0.15s ease';
          labelEl.style.userSelect = 'none';
          const d = node.data as any;
          labelEl.textContent = d?.label || nodeId;
          overlay.appendChild(labelEl);
          labelElements.set(nodeId, labelEl);
        }

        // NOTE: 标签定位在球体下方，偏移量与节点半径相关
        labelEl.style.left = `${screenX}px`;
        labelEl.style.top = `${screenY + 18}px`;
        labelEl.style.opacity = '1';
      }

      // 移除已不存在的节点标签
      for (const [id, el] of labelElements) {
        if (!activeIds.has(id)) {
          el.remove();
          labelElements.delete(id);
        }
      }

      labelsRafRef.current = requestAnimationFrame(updateLabels);
    };

    labelsRafRef.current = requestAnimationFrame(updateLabels);

    return () => {
      cancelAnimationFrame(labelsRafRef.current);
      // 清理所有标签 DOM
      labelElements.forEach((el) => el.remove());
      labelElements.clear();
    };
  }, [viewMode, data]);

  /**
   * 布局切换处理
   */
  const handleLayoutChange = useCallback(async (type: 'TB' | 'LR') => {
    setLayoutType(type);
    if (graphRef.current) {
      graphRef.current.setLayout(getLayoutConfig(type));
      await graphRef.current.layout();
      graphRef.current.fitCenter();
    }
  }, [getLayoutConfig]);

  const handleOuterClick = useCallback(() => {
    setSelectedEdgeInfo(null);
  }, []);

  /**
   * 一键定位：将图谱居中并自适应缩放到视口
   */
  const handleFitView = useCallback(() => {
    if (graphRef.current) {
      fitViewReadable(graphRef.current);
    }
  }, []);

  return (
    <div
      className="knowledge-graph-shell"
      style={{
        width: '100%',
        height: '100%',
        position: 'relative',
        background: viewMode === '3d' ? '#0d0c16' : 'transparent',
        transition: 'background 0.5s ease',
        overflow: 'hidden'
      }}
      onClick={handleOuterClick}
    >
      {/* 3D 星空背景层 */}
      {viewMode === '3d' && (
        <div style={{
          position: 'absolute',
          top: 0, left: 0, right: 0, bottom: 0,
          background: 'radial-gradient(ellipse at center, #1d1834 0%, #0d0c16 100%)',
          zIndex: 0,
          pointerEvents: 'none',
        }}>
          <div style={{
            position: 'absolute',
            width: '100%',
            height: '100%',
            backgroundImage: `
              radial-gradient(1px 1px at 25px 5px, white, rgba(255,255,255,0)),
              radial-gradient(1.5px 1.5px at 50px 25px, white, rgba(255,255,255,0)),
              radial-gradient(1px 1px at 125px 20px, white, rgba(255,255,255,0)),
              radial-gradient(2px 2px at 50px 75px, white, rgba(255,255,255,0)),
              radial-gradient(2px 2px at 15px 125px, white, rgba(255,255,255,0)),
              radial-gradient(1.5px 1.5px at 110px 80px, white, rgba(255,255,255,0)),
              radial-gradient(1px 1px at 180px 140px, white, rgba(255,255,255,0)),
              radial-gradient(2.5px 2.5px at 220px 40px, white, rgba(255,255,255,0)),
              radial-gradient(1.5px 1.5px at 300px 90px, white, rgba(255,255,255,0)),
              radial-gradient(1px 1px at 350px 150px, white, rgba(255,255,255,0)),
              radial-gradient(2px 2px at 400px 30px, white, rgba(255,255,255,0)),
              radial-gradient(2.5px 2.5px at 480px 120px, white, rgba(255,255,255,0)),
              radial-gradient(1px 1px at 450px 200px, white, rgba(255,255,255,0))
            `,
            backgroundRepeat: 'repeat',
            backgroundSize: '500px 300px',
            opacity: 0.6,
            animation: 'twinkle 5s infinite alternate ease-in-out'
          }} />
        </div>
      )}

      {/* 工具栏 */}
      {data && (
        <div
          className="graph-toolbar"
          style={{
            position: 'absolute',
            top: 16,
            left: 16,
            right: 16,
            zIndex: 10,
            display: 'flex',
            gap: 8,
            flexWrap: 'wrap',
            justifyContent: 'flex-end',
          }}
        >
          {/* 2D 模式专属：布局切换按钮 */}
          {viewMode === '2d' && (
            <>
              {(['TB', 'LR'] as const).map((type) => (
                <button
                  key={type}
                  onClick={(e) => { e.stopPropagation(); handleLayoutChange(type); }}
                  data-active={layoutType === type}
                  style={{ padding: '7px 16px', border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}
                >
                  {type === 'TB' ? t('graph.layout_vertical') : t('graph.layout_horizontal')}
                </button>
              ))}

              {/* 一键定位按钮 */}
              <button
                onClick={(e) => { e.stopPropagation(); handleFitView(); }}
                title="一键定位：自适应视口"
                style={{ padding: '7px 14px', border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}
              >
                {t('graph.fit_view')}
              </button>
            </>
          )}

          {/* 3D 专属：显示隐藏连线按钮 */}
          {viewMode === '3d' && (
            <>
              <button
                onClick={(e) => { e.stopPropagation(); setShow3DEdges(!show3DEdges); }}
                data-active={show3DEdges}
                style={{ padding: '7px 14px', border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}
                title={show3DEdges ? t('graph.hide_edges') : t('graph.show_edges')}
              >
                {show3DEdges ? t('graph.edges_on') : t('graph.edges_off')}
              </button>
            </>
          )}

          {/* 2D/3D 模式切换按钮 */}
          {(['2d', '3d'] as const).map((mode) => (
            <button
              key={mode}
              onClick={(e) => {
                e.stopPropagation();
                if (viewMode !== mode) {
                  // NOTE: 只清引用，销毁由 useEffect cleanup 统一处理
                  // 不要在这里调 destroy()，否则 useEffect cleanup 会双重销毁导致崩溃
                  graphRef.current = null;
                  setHoveredNode(null);
                  setSelectedNodeInfo(null);
                  setSelectedEdgeInfo(null);
                  setViewMode(mode);
                  if (onViewModeChange) {
                    onViewModeChange(mode);
                  }
                }
              }}
              data-active={viewMode === mode}
              style={{ padding: '7px 14px', border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}
            >
              {mode === '2d' ? '2D' : '3D'}
            </button>
          ))}
        </div>
      )}

      {/* G6 画布容器 */}
      <div
        ref={containerRef}
        style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}
        onClick={(e) => e.stopPropagation()}
      />

      {/* 3D 模式下的节点名称标签覆盖层 */}
      {viewMode === '3d' && (
        <div
          ref={labelsOverlayRef}
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            pointerEvents: 'none',
            zIndex: 5,
            overflow: 'hidden',
          }}
        />
      )}


      {/* 底部节点信息面板（2D/3D 通用，扁平化布局） */}
      {selectedNodeInfo && (
        <div
          className="glass glass--thick glass--grain glass--lit graph-info-panel"
          style={{
            position: 'absolute',
            left: 16,
            right: 16,
            bottom: 16,
            maxHeight: 'calc(100% - 100px)',
            overflowY: 'auto',
            padding: '16px 22px',
            zIndex: 40,
            animation: 'fadeIn 0.2s ease-out',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* 关闭按钮 */}
          <button
            onClick={() => setSelectedNodeInfo(null)}
            style={{
              position: 'absolute',
              top: 10,
              right: 14,
              background: 'none',
              border: 'none',
              fontSize: 16,
              color: 'hsl(var(--muted-foreground))',
              cursor: 'pointer',
              lineHeight: 1,
              padding: 4,
            }}
          >
            ✕
          </button>

          {/* 顶部横排：节点名称 + 掌握度 */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 20,
            marginBottom: 12,
            paddingBottom: 10,
            borderBottom: '1px solid hsl(var(--foreground) / .14)',
            paddingRight: 30,
          }}>
            <div style={{
              fontSize: 16,
              fontWeight: 700,
              color: 'hsl(var(--foreground))',
              whiteSpace: 'nowrap',
              flexShrink: 0,
            }}>
              {selectedNodeInfo.name}
            </div>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flex: 1,
              minWidth: 120,
            }}>
              <span style={{ fontSize: 12, color: 'hsl(var(--muted-foreground))', fontWeight: 500, whiteSpace: 'nowrap', flexShrink: 0 }}>
                {t('graph.mastery')}
              </span>
              <div style={{
                flex: 1,
                height: 6,
                borderRadius: 3,
                background: 'hsl(var(--foreground) / .14)',
                overflow: 'hidden',
              }}>
                <div style={{
                  height: '100%',
                  width: `${selectedNodeInfo.mastery * 100}%`,
                  borderRadius: 3,
                  background: 'linear-gradient(90deg, hsl(var(--primary)), hsl(var(--accent)))',
                  transition: 'width 0.5s ease',
                }} />
              </div>
              <span style={{
                fontSize: 13,
                fontWeight: 700,
                color: selectedNodeInfo.mastery > 0.6 ? 'hsl(var(--accent))' : selectedNodeInfo.mastery > 0.3 ? 'hsl(var(--primary))' : 'hsl(var(--muted-foreground))',
                whiteSpace: 'nowrap',
                flexShrink: 0,
              }}>
                {Math.round(selectedNodeInfo.mastery * 100)}%
              </span>
            </div>
          </div>

          {/* 前驱 & 后续知识横排展示 */}
          <div style={{
            display: 'flex',
            gap: 24,
            flexWrap: 'wrap',
          }}>
            {/* 前驱知识（父节点） */}
            {selectedNodeInfo.parents.length > 0 && (
              <div style={{ flex: 1, minWidth: 200 }}>
                <div style={{
                  fontSize: 11,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  color: 'hsl(var(--accent))',
                  marginBottom: 6,
                }}>
                  ▲ {t('graph.prerequisites') || '前驱知识'}
                </div>
                {selectedNodeInfo.parents.map((p, i) => (
                  <div key={i} style={{
                    fontSize: 13,
                    color: 'hsl(var(--foreground) / .88)',
                    padding: '4px 0',
                    borderBottom: i < selectedNodeInfo.parents.length - 1 ? '1px solid hsl(var(--foreground) / .1)' : 'none',
                    display: 'flex',
                    alignItems: 'baseline',
                    gap: 6,
                  }}>
                    <span style={{ color: 'hsl(var(--accent))', fontSize: 10, flexShrink: 0 }}>●</span>
                    <span style={{ fontWeight: 500, flexShrink: 0 }}>{p.name}</span>
                    {p.relation && (
                      <span style={{ fontSize: 13, color: 'hsl(var(--muted-foreground))', fontStyle: 'italic' }}>
                        {p.relation}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* 后续知识（子节点） */}
            {selectedNodeInfo.children.length > 0 && (
              <div style={{ flex: 1, minWidth: 200 }}>
                <div style={{
                  fontSize: 11,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  color: 'hsl(var(--primary))',
                  marginBottom: 6,
                }}>
                  ▼ {t('graph.subsequent') || '后续知识'}
                </div>
                {selectedNodeInfo.children.map((c, i) => (
                  <div key={i} style={{
                    fontSize: 13,
                    color: 'hsl(var(--foreground) / .88)',
                    padding: '4px 0',
                    borderBottom: i < selectedNodeInfo.children.length - 1 ? '1px solid hsl(var(--foreground) / .1)' : 'none',
                    display: 'flex',
                    alignItems: 'baseline',
                    gap: 6,
                  }}>
                    <span style={{ color: 'hsl(var(--primary))', fontSize: 10, flexShrink: 0 }}>●</span>
                    <span style={{ fontWeight: 500, flexShrink: 0 }}>{c.name}</span>
                    {c.relation && (
                      <span style={{ fontSize: 13, color: 'hsl(var(--muted-foreground))', fontStyle: 'italic' }}>
                        {c.relation}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 无父子关系时的提示 */}
          {selectedNodeInfo.parents.length === 0 && selectedNodeInfo.children.length === 0 && (
            <div style={{ fontSize: 12, color: 'hsl(var(--muted-foreground))', fontStyle: 'italic', textAlign: 'center', padding: '8px 0' }}>
              此节点暂无关联关系
            </div>
          )}
        </div>
      )}

      {/* 边关系详情弹窗 - 精致的浮动卡片 */}
      {selectedEdgeInfo && (
        <div
          className="glass glass--thick glass--grain glass--lit graph-info-panel"
          style={{
            position: 'fixed',
            left: selectedEdgeInfo.x,
            top: selectedEdgeInfo.y,
            transform: 'translate(-50%, -100%)',
            marginTop: '-12px',
            minWidth: '220px',
            maxWidth: '320px',
            padding: '16px',
            zIndex: 50,
            animation: 'fadeInUp 0.2s ease-out',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div style={{
            fontSize: 11,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
            color: 'hsl(var(--primary))',
            marginBottom: 8,
          }}>
            {t('graph.relation_info')}
          </div>
          <div style={{
            fontSize: 13,
            color: 'hsl(var(--foreground) / .88)',
            lineHeight: 1.5,
            marginBottom: 12,
          }}>
            {selectedEdgeInfo.label}
          </div>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}>
            <span style={{ fontSize: 11, color: 'hsl(var(--muted-foreground))', fontWeight: 500 }}>
              {t('graph.relation_strength')}
            </span>
            <div style={{
              flex: 1,
              height: 4,
              borderRadius: 2,
              background: 'hsl(var(--foreground) / .14)',
              overflow: 'hidden',
            }}>
              <div style={{
                height: '100%',
                width: `${selectedEdgeInfo.weight * 100}%`,
                borderRadius: 2,
                background: 'linear-gradient(90deg, hsl(var(--primary)), hsl(var(--accent)))',
                transition: 'width 0.3s ease',
              }} />
            </div>
            <span style={{
              fontSize: 12,
              fontWeight: 600,
              color: 'hsl(var(--primary))',
              minWidth: 36,
              textAlign: 'right',
            }}>
              {Math.round(selectedEdgeInfo.weight * 100)}%
            </span>
          </div>
        </div>
      )}

      {/* 内联动画关键帧 */}
      <style>{`
        @keyframes fadeInUp {
          from { opacity: 0; transform: translate(-50%, -100%) translateY(8px); }
          to { opacity: 1; transform: translate(-50%, -100%) translateY(0); }
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes twinkle {
          0% { opacity: 0.3; }
          100% { opacity: 0.8; }
        }
      `}</style>
    </div>
  );
};

export default KnowledgeGraph;
