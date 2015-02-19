import os
import glob
from collections import OrderedDict
import sys
from SCons.Script import Command, Depends
# SConscript('test/SConscript', duplicate=0)

# `scons test`
Alias('test', 'test/_results/ALL.passed')

Alias('validate', '_output/validation/valid.out')
Command('_output/validation/valid.out', './bin/run-driver.py', './bin/run-driver.py --label validation --plotdir _output/validation/plots --datafname test/A-every-100-subset-0.tsv.bz2 && touch $TARGET')

# ----------------------------------------------------------------------------------------

env = Environment(ENV=os.environ, SHELL='/bin/bash')
sys.path.append(os.getenv('HOME') + '/bin')

testoutdir = '_output/test'
if not os.path.exists(testoutdir):
    os.makedirs(testoutdir)
cmd = './bin/partis.py'
base_cmd = './bin/run-driver.py --label test --extra-args __seed:1:--no-plot --datafname test/A-every-100-subset-0.tsv.bz2 --plotdir ' + testoutdir + '/plots --n-queries 250 --n-sim-events 50 --n-procs 5'
actions = OrderedDict()
actions['cache-data-parameters'] = 'data'  # key is name, value is target (note that the target corresponds to a directory or file in <testoutdir>
actions['simulate'] = 'simu.csv'
actions['cache-simu-parameters'] = 'simu'
actions['plot-performance'] = 'plots'

tests = OrderedDict()
existing_parameter_dir = 'test/regression/parameters/simu/hmm'
# first add the simple, few-sequence tests (using partis.py)
tests['single-point-estimate'] = cmd + ' --run-algorithm viterbi --seqfile test/regression/parameters/simu.csv --parameter-dir ' + existing_parameter_dir + ' --n-max-queries 1 --debug 1'
tests['partition-a-few'] = cmd + ' --partition --seqfile test/regression/parameters/simu.csv --parameter-dir ' + existing_parameter_dir + ' --n-max-queries 8 --debug 1 --truncate-pairs'
tests['viterbi-pair'] = cmd + ' --run-algorithm viterbi --n-sets 2 --all-combinations --seqfile test/regression/parameters/simu.csv --parameter-dir ' + existing_parameter_dir + ' --debug 1 --truncate-pairs --n-max-queries 3'
# then add the tests that run over the framework (using run-driver.py)
for action in actions:
    tests[action] = base_cmd + ' --action ' + action

all_passed = 'test/_results/ALL.passed'
individual_passed = ['test/_results/{}.passed'.format(name) for name in tests.keys()]

for path in individual_passed + [all_passed]:
    if os.path.exists(path):
        print 'removing', path
        os.remove(path)

for name, test_cmd in tests.items():
    out = 'test/_results/%s.out' % name
    Depends(out, glob.glob('python/*.py') + ['packages/ham/bcrham',])
    if name in actions:
        target = actions[name]  # some targets are dirs, which scons doesn't handle, but either way we want to diff the whole target
        env.Command(out, cmd, test_cmd + ' && touch $TARGET')  # it's kind of silly to put partis.py as the SOURCE, but you've got to put *something*, and we've already got the deps covered...
        env.Command('test/_results/%s.passed' % name,
                    ['test/regression/parameters/' + target, testoutdir + '/' + target, out],
                    'diff -qr  -x\'*.svg\' -x params -x plots.html ${SOURCES[0]} ${SOURCES[1]} && touch $TARGET')
    else:
        env.Command(out, cmd, test_cmd + ' >$TARGET')
        # touch a sentinel `passed` file if we get what we expect
        env.Command('test/_results/%s.passed' % name,
                [out, 'test/regression/%s.out' % name],
                'diff -ub -I \'time:\' ${SOURCES[0]} ${SOURCES[1]} && touch $TARGET')  # ignore the lines telling you how long things took

# Set up sentinel dependency of all passed on the individual_passed sentinels.
Command(all_passed,
        individual_passed,
        'cat $SOURCES > $TARGET')
