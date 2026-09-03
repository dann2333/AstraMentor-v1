import React from 'react';
import { ChevronDown } from 'lucide-react';
import type { LearnerState } from '../../types';
import { useLanguage } from '../../contexts/LanguageContext';

import type { GraphData } from '../../types';

interface DashboardProps {
  state: LearnerState | null;
  graphData?: GraphData | null;
  viewMode?: '2d' | '3d';
}

const Dashboard: React.FC<DashboardProps> = ({ state, graphData }) => {
  const { t } = useLanguage();
  const [isOpen, setIsOpen] = React.useState(true);
  
  // Logic: 
  // If graphData is present, show stats for the CURRENT GRAPH (Total nodes, etc.)
  // If no graphData, show global learner state.
  
  // Actually, user wants data to match the generated star chart.
  // So if graphData exists, we prioritize it.
  
  const displayState = React.useMemo(() => {
    if (graphData && graphData.nodes.length > 0) {
        const total = graphData.nodes.length;
        
        // Mastered: Current Mastery (A) >= Target Mastery (B, default 0.8)
        const mastered = graphData.nodes.filter(n => {
            const current = parseFloat(String(n.attributes.weight_A || 0));
            const target = parseFloat(String(n.attributes.weight_B || 0.8));
            return current >= target;
        }).length;

        // Average: Simple average of Current Mastery
        const average = total > 0 
            ? graphData.nodes.reduce((acc, n) => acc + parseFloat(String(n.attributes.weight_A || 0)), 0) / total 
            : 0;
            
        return {
            total,
            mastered,
            average_mastery: average
        };
    }
    return state;
  }, [state, graphData]);

  if (!displayState) return null;

  return (
    // 三张方卡合成了一条窄横幅。原来那组卡片占掉星图左上角一大块，
    // 节点一旦被布局排到那儿就直接压在数字上；而且"三个大数字卡"本身
    // 也是最没记忆点的那种仪表盘写法。
    <div className="dashboard-shell pointer-events-auto">
      <div className="dashboard-bar glass glass--thin glass--grain glass--lit">
        {isOpen && (
          <>
            <span className="dashboard-bar__item">
              <em>{t('dashboard.total')}</em>
              <b>{displayState.total}</b>
            </span>
            <i className="dashboard-bar__sep" />
            <span className="dashboard-bar__item">
              <em>{t('dashboard.mastered')}</em>
              <b className="dashboard-bar__value--done">{displayState.mastered}</b>
            </span>
            <i className="dashboard-bar__sep" />
            <span className="dashboard-bar__item">
              <em>{t('dashboard.average_mastery')}</em>
              <b>{(displayState.average_mastery * 100).toFixed(1)}%</b>
            </span>
          </>
        )}
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className="dashboard-bar__toggle"
          aria-expanded={isOpen}
          title={isOpen ? t('dashboard.collapse') : t('dashboard.expand')}
        >
          <ChevronDown size={13} style={{ transform: isOpen ? 'rotate(180deg)' : 'none', transition: 'transform .25s var(--glass-ease-out)' }} />
        </button>
      </div>
    </div>
  );
};


export default Dashboard;
