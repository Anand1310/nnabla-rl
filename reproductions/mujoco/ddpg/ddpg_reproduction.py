import argparse

import nnabla_rl
import nnabla_rl.algorithms as A
import nnabla_rl.hooks as H
import nnabla_rl.writers as W
from nnabla_rl.utils.evaluator import EpisodicEvaluator
from nnabla_rl.utils.reproductions import build_mujoco_env, set_global_seed
from nnabla_rl.utils import serializers


def select_start_timesteps(env_name):
    if env_name in ['Ant-v2', 'HalfCheetah-v2']:
        timesteps = 10000
    else:
        timesteps = 1000
    print(f'Selected start timesteps: {timesteps}')
    return timesteps


def run_training(args):
    nnabla_rl.run_on_gpu(cuda_device_id=args.gpu)

    outdir = f'{args.env}_results/seed-{args.seed}'
    set_global_seed(args.seed)

    eval_env = build_mujoco_env(args.env, test=True, seed=args.seed + 100)
    evaluator = EpisodicEvaluator(run_per_evaluation=10)
    evaluation_hook = H.EvaluationHook(eval_env,
                                       evaluator,
                                       timing=5000,
                                       writer=W.FileWriter(outdir=outdir, file_prefix='evaluation_result'))

    iteration_num_hook = H.IterationNumHook(timing=100)
    save_snapshot_hook = H.SaveSnapshotHook(outdir, timing=5000)

    train_env = build_mujoco_env(args.env, seed=args.seed, render=args.render)
    if args.snapshot_dir is None:
        timesteps = select_start_timesteps(args.env)
        params = A.DDPGParam(start_timesteps=timesteps)
        ddpg = A.DDPG(train_env, params=params)
    else:
        ddpg = serializers.load_snapshot(args.snapshot_dir)
    hooks = [iteration_num_hook, save_snapshot_hook, evaluation_hook]
    ddpg.set_hooks(hooks)

    ddpg.train_online(train_env, total_iterations=1000000)

    eval_env.close()
    train_env.close()


def run_showcase(args):
    nnabla_rl.run_on_gpu(cuda_device_id=args.gpu)

    if args.snapshot_dir is None:
        raise ValueError(
            'Please specify the snapshot dir for showcasing')
    ddpg = serializers.load_snapshot(args.snapshot_dir)
    if not isinstance(ddpg, A.DDPG):
        raise ValueError('Loaded snapshot is not trained with DDPG!')

    eval_env = build_mujoco_env(
        args.env, test=True, seed=args.seed + 200, render=True)
    evaluator = EpisodicEvaluator()
    evaluator(ddpg, eval_env)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env', type=str, default='Ant-v2')
    parser.add_argument('--gpu', type=int, default=0)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--showcase', action='store_true')
    parser.add_argument('--snapshot-dir', type=str, default=None)

    args = parser.parse_args()

    if args.showcase:
        run_showcase(args)
    else:
        run_training(args)


if __name__ == '__main__':
    main()
