import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import { useState, useEffect, useRef } from 'react';

interface StrategySchema {
  name: string;
  param_schema: Array<{ name: string; type: string; default: unknown; description: string }>;
}

interface StrategyListResponse {
  strategies: StrategySchema[];
}

interface ConfigResponse {
  symbol: string;
  strategy_name: string | null;
  parameters: Record<string, unknown>;
}

const StrategySettings = () => {
  const queryClient = useQueryClient();
  const [savingSymbol, setSavingSymbol] = useState<string | null>(null);

  const { data: statusData } = useQuery({
    queryKey: ['status'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/status');
      return data;
    },
  });

  const { data: listData } = useQuery<StrategyListResponse>({
    queryKey: ['strategies-list'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/strategies/list');
      return data;
    },
  });

  const symbols = statusData?.targetSymbols ?? [];
  const strategies = listData?.strategies ?? [];

  const saveMutation = useMutation({
    mutationFn: async (body: { symbol: string; strategy_name: string; parameters: Record<string, unknown> }) => {
      await apiClient.post('/api/strategies/config', body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['status'] });
      queryClient.invalidateQueries({ queryKey: ['strategies-config'] });
      setSavingSymbol(null);
    },
  });

  return (
    <div className="bg-gray-800 p-6 rounded-xl shadow-lg">
      <h3 className="text-lg font-medium text-gray-200 mb-4">종목별 전략 설정</h3>
      <div className="space-y-6">
        {symbols.map((symbol) => (
          <SymbolStrategyForm
            key={symbol}
            symbol={symbol}
            strategies={strategies}
            onSave={(strategyName, parameters) => {
              setSavingSymbol(symbol);
              saveMutation.mutate({ symbol, strategy_name: strategyName, parameters });
            }}
            isSaving={savingSymbol === symbol}
          />
        ))}
      </div>
      {symbols.length === 0 && (
        <p className="text-gray-500">설정된 종목이 없습니다.</p>
      )}
    </div>
  );
};

function SymbolStrategyForm({
  symbol,
  strategies,
  onSave,
  isSaving,
}: {
  symbol: string;
  strategies: StrategySchema[];
  onSave: (strategyName: string, parameters: Record<string, unknown>) => void;
  isSaving: boolean;
}) {
  const [strategyName, setStrategyName] = useState<string>('');
  const [params, setParams] = useState<Record<string, unknown>>({});

  const { data: config } = useQuery<ConfigResponse>({
    queryKey: ['strategies-config', symbol],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/strategies/config/${symbol}`);
      return data;
    },
    enabled: !!symbol,
  });

  const syncedRef = useRef(false);
  useEffect(() => {
    if (config && !syncedRef.current) {
      syncedRef.current = true;
      setStrategyName(config.strategy_name || 'volatility_breakout');
      setParams((config.parameters ?? {}) as Record<string, unknown>);
    }
  }, [config]);

  const schema = strategies.find((s) => s.name === (strategyName || config?.strategy_name));
  const paramSchema = schema?.param_schema ?? [];

  const effectiveStrategy = strategyName || config?.strategy_name || 'volatility_breakout';
  const effectiveParams = Object.keys(params).length ? params : (config?.parameters ?? {});

  const handleStrategyChange = (name: string) => {
    setStrategyName(name);
    const s = strategies.find((x) => x.name === name);
    const next: Record<string, unknown> = {};
    s?.param_schema.forEach((p) => {
      next[p.name] = effectiveParams[p.name] ?? p.default;
    });
    setParams(next);
  };

  const handleParamChange = (name: string, value: string | number) => {
    setParams((prev) => ({ ...prev, [name]: value }));
  };

  return (
    <div className="border border-gray-700 rounded-lg p-4">
      <div className="flex flex-wrap items-center gap-4 mb-3">
        <span className="font-medium text-gray-200">{symbol}</span>
        <select
          className="bg-gray-700 text-gray-200 rounded px-3 py-1.5"
          value={effectiveStrategy}
          onChange={(e) => handleStrategyChange(e.target.value)}
        >
          {strategies.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name === 'volatility_breakout' ? '변동성 돌파' : s.name === 'ma_crossover' ? '이평 교차' : s.name === 'rsi' ? 'RSI' : s.name === 'bollinger' ? '볼린저' : s.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="bg-green-700 hover:bg-green-600 text-white px-3 py-1.5 rounded disabled:opacity-50"
          disabled={isSaving}
          onClick={() => onSave(effectiveStrategy, effectiveParams)}
        >
          {isSaving ? '저장 중…' : '저장'}
        </button>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {paramSchema.map((p) => (
          <label key={p.name} className="flex flex-col gap-0.5">
            <span className="text-gray-400 text-xs">{p.description || p.name}</span>
            {p.type === 'int' || p.type === 'float' ? (
              <input
                type="number"
                step={p.type === 'float' ? 0.1 : 1}
                className="bg-gray-700 text-gray-200 rounded px-2 py-1 text-sm"
                value={String(effectiveParams[p.name] ?? p.default ?? '')}
                onChange={(e) =>
                  handleParamChange(p.name, p.type === 'float' ? parseFloat(e.target.value) || 0 : parseInt(e.target.value, 10) || 0)
                }
              />
            ) : (
              <input
                type="text"
                className="bg-gray-700 text-gray-200 rounded px-2 py-1 text-sm"
                value={String(effectiveParams[p.name] ?? p.default ?? '')}
                onChange={(e) => handleParamChange(p.name, e.target.value)}
              />
            )}
          </label>
        ))}
      </div>
    </div>
  );
}

export default StrategySettings;
