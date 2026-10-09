import assert from 'node:assert/strict';
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer, type Server } from 'node:http';
import test from 'node:test';
import { openWorkshopGenerator } from './workshop-generator.ts';

const motion = Buffer.alloc(12);
motion.write('glTF', 0, 'ascii');
motion.writeUInt32LE(2, 4);
motion.writeUInt32LE(12, 8);

type Harness = {
  launch: (exe: string, args: string[]) => ChildProcess;
  requests: Array<{ text: string; seconds: number; seed: number }>;
  args: string[];
  stop: () => Promise<void>;
  exited: () => boolean;
};

function harness({ healthWait = 0, status = 200, bytes = motion } = {}): Harness {
  const requests: Harness['requests'] = [];
  let args: string[] = [];
  let server: Server | undefined;
  let child: ChildProcess | undefined;
  let gone = false;
  let healthChecks = 0;
  return {
    requests,
    get args() { return args; },
    launch: (_exe, argv) => {
      args = argv;
      const port = Number(argv[argv.indexOf('--port') + 1]);
      server = createServer(async (req, res) => {
        if (req.url === '/health') {
          res.writeHead(healthChecks++ < healthWait ? 503 : 200).end();
        } else if (req.url === '/motion') {
          let text = '';
          for await (const chunk of req) text += chunk;
          requests.push(JSON.parse(text));
          res.writeHead(status, { 'content-type': 'model/gltf-binary' }).end(bytes);
        } else res.writeHead(404).end();
      });
      server.listen(port, '127.0.0.1');
      // 子の動きはEOFのみ。生成モデル、実Python、本番イデアを使わない。
      child = spawn(process.execPath, ['-e', 'process.stdin.resume();process.stdin.on("end",()=>process.exit(0))'], {
        stdio: ['pipe', 'ignore', 'ignore'], windowsHide: true,
      });
      child.once('exit', () => { gone = true; });
      return child;
    },
    exited: () => gone,
    stop: async () => {
      child?.stdin?.end();
      if (child && !gone) await new Promise(resolve => child!.once('exit', resolve));
      if (server) await new Promise<void>(resolve => server!.close(() => resolve()));
    },
  };
}

test('工房印は子の引数にあるが願いの名前と英文はなく、HTTPのみで生成する', async t => {
  const fake = harness({ healthWait: 2 });
  t.after(fake.stop);
  const controller = new AbortController();
  const generator = await openWorkshopGenerator({
    python: 'unused', script: 'generator.py', waitCapacitySeconds: 180,
    startupMs: 3000, launch: fake.launch,
  }, controller.signal);
  const result = await generator.generate({ text: 'a person raises both arms', seconds: 4 }, 123, controller.signal);
  assert.equal(result.kind, 'candidate');
  if (result.kind === 'candidate') assert.deepEqual(result.bytes, motion);
  assert.deepEqual(fake.requests, [{ text: 'a person raises both arms', seconds: 4, seed: 123 }]);
  assert.ok(fake.args.includes('--nirai-workshop'));
  assert.equal(fake.args[fake.args.indexOf('--wait-capacity-seconds') + 1], '180');
  assert.equal(fake.args.join(' ').includes('raises both arms'), false);
  await generator.close();
  assert.equal(fake.exited(), true);
});

test('生成失敗はseed固有、不正候補は置かず、通信不能は願いに数えない', async t => {
  const fake = harness({ status: 500 });
  t.after(fake.stop);
  const controller = new AbortController();
  const generator = await openWorkshopGenerator({
    python: 'unused', script: 'generator.py', waitCapacitySeconds: 0, launch: fake.launch,
  }, controller.signal);
  assert.equal((await generator.generate({ text: 'a person waves', seconds: 2 }, 1, controller.signal)).kind, 'rejected');
  controller.abort();
  assert.equal((await generator.generate({ text: 'a person waves', seconds: 2 }, 2, controller.signal)).kind, 'unavailable');
  await generator.close();
  assert.equal(fake.exited(), true);
});

test('開始できないときも子を残さない', async t => {
  const fake = harness({ healthWait: 100 });
  t.after(fake.stop);
  await assert.rejects(openWorkshopGenerator({
    python: 'unused', script: 'generator.py', waitCapacitySeconds: 0,
    startupMs: 300, launch: fake.launch,
  }, new AbortController().signal), /generator unavailable/);
  assert.equal(fake.exited(), true);
});

test('VRMAのヘッダーが壊れていれば候補にしない', async t => {
  const fake = harness({ bytes: Buffer.from('not a glb') });
  t.after(fake.stop);
  const signal = new AbortController().signal;
  const generator = await openWorkshopGenerator({
    python: 'unused', script: 'generator.py', waitCapacitySeconds: 0, launch: fake.launch,
  }, signal);
  assert.equal((await generator.generate({ text: 'a person stands', seconds: 2 }, 3, signal)).kind, 'rejected');
  await generator.close();
});
