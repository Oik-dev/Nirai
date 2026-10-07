// 脳を起こさず、同じメーターの数字をJSONで取得する。
import { collectUsage, meterDefaults } from './usage.ts';
import { settings } from './settings.ts';

try {
  const args = process.argv.slice(2);
  const dates: { from?: string; to?: string } = {};
  for (let i = 0; i < args.length; i += 2) {
    if (!['--from', '--to'].includes(args[i]) || !args[i + 1]) throw new Error('usage');
    dates[args[i].slice(2) as 'from' | 'to'] = args[i + 1];
  }
  console.log(JSON.stringify(await collectUsage({ ...meterDefaults(settings.residentsRoot, settings.repoRoot), ...dates }), null, 2));
} catch {
  console.error('集計できません。--from YYYY-MM-DD --to YYYY-MM-DD と記録の読み取り権限を確かめてください。');
  process.exitCode = 1;
}
