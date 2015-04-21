import time
import sys
import json
import itertools
import shutil
import math
import os
import glob
import csv
import re
from multiprocessing import Pool
from subprocess import Popen, check_call

import utils
from opener import opener
from seqfileopener import get_seqfile_info
from clusterer import Clusterer
from waterer import Waterer
from hist import Hist
from parametercounter import ParameterCounter
from performanceplotter import PerformancePlotter
import viterbicluster
import plotting

## ----------------------------------------------------------------------------------------
def from_same_event(is_data, pair_hmm, reco_info, query_names):
    if is_data:
        return False
    if len(query_names) > 1:
        reco_id = reco_info[query_names[0]]['reco_id']  # the first one's reco id
        for iq in range(1, len(query_names)):  # then loop through the rest of 'em to see if they're all the same
            if reco_id != reco_info[query_names[iq]]['reco_id']:
                return False
        return True
    else:
        return False

# ----------------------------------------------------------------------------------------
class PartitionDriver(object):
    def __init__(self, args):
        self.args = args
        self.germline_seqs = utils.read_germlines(self.args.datadir)  #, add_fp=True)

        with opener('r')(self.args.datadir + '/v-meta.json') as json_file:  # get location of <begin> cysteine in each v region
            self.cyst_positions = json.load(json_file)
        with opener('r')(self.args.datadir + '/j_tryp.csv') as csv_file:  # get location of <end> tryptophan in each j region (TGG)
            tryp_reader = csv.reader(csv_file)
            self.tryp_positions = {row[0]:row[1] for row in tryp_reader}  # WARNING: this doesn't filter out the header line

        self.precluster_info = {}

        if self.args.seqfile is not None:
            self.input_info, self.reco_info = get_seqfile_info(self.args.seqfile, self.args.is_data,
                                                               self.germline_seqs, self.cyst_positions, self.tryp_positions,
                                                               self.args.n_max_queries, self.args.queries, self.args.reco_ids)

        self.outfile = None
        if self.args.outfname != None:
            if os.path.exists(self.args.outfname):
                os.remove(self.args.outfname)
            self.outfile = open(self.args.outfname, 'a')

    # ----------------------------------------------------------------------------------------
    def clean(self, waterer):
        if self.args.no_clean:
            return
        if not self.args.read_cached_parameters:  # ha ha I don't have this arg any more
            waterer.clean()  # remove all the parameter files that the waterer's ParameterCounter made
            for fname in glob.glob(waterer.parameter_dir + '/hmms/*.yaml'):  # remove all the hmm files from the same directory
                os.remove(fname)
            os.rmdir(waterer.parameter_dir + '/hmms')
            os.rmdir(waterer.parameter_dir)

        # check to see if we missed anything
        leftovers = [ fname for fname in os.listdir(self.args.workdir) ]
        for over in leftovers:
            print self.args.workdir + '/' + over
        os.rmdir(self.args.workdir)

    # ----------------------------------------------------------------------------------------
    def cache_parameters(self):
        assert self.args.n_sets == 1  # er, could do it for n > 1, but I'd want to think through a few things first
        assert self.args.plotdir is not None

        sw_parameter_dir = self.args.parameter_dir + '/sw'
        waterer = Waterer(self.args, self.input_info, self.reco_info, self.germline_seqs, parameter_dir=sw_parameter_dir, write_parameters=True, plotdir=self.args.plotdir + '/sw')
        waterer.run()
        self.write_hmms(sw_parameter_dir, waterer.info['all_best_matches'])

        parameter_in_dir = sw_parameter_dir
        for ibw in range(self.args.baum_welch_iterations):
            parameter_out_dir = self.args.parameter_dir + '/hmm'
            hmm_plotdir = self.args.plotdir + '/hmm'
            if self.args.baum_welch_iterations > 1:
                parameter_out_dir += '-' + str(ibw)
                hmm_plotdir += '-' + str(ibw)
            self.run_hmm('viterbi', waterer.info, parameter_in_dir=parameter_in_dir, parameter_out_dir=parameter_out_dir, hmm_type='k=1', count_parameters=True, plotdir=hmm_plotdir)
            self.write_hmms(parameter_out_dir, waterer.info['all_best_matches'])
            parameter_in_dir = parameter_out_dir

        if not self.args.no_clean:
            os.rmdir(self.args.workdir)

    # ----------------------------------------------------------------------------------------
    def partition(self):
        assert os.path.exists(self.args.parameter_dir)

        # run smith-waterman
        waterer = Waterer(self.args, self.input_info, self.reco_info, self.germline_seqs, parameter_dir=self.args.parameter_dir, write_parameters=False)
        waterer.run()

        # cdr3 length partitioning
        cdr3_cluster = False  # don't precluster on cdr3 length for the moment -- I cannot accurately infer cdr3 length in some sequences, so I need a way to pass query seqs to the clusterer with several possible cdr3 lengths (and I don't know how to do that!)
        cdr3_length_clusters = None
        if cdr3_cluster:
            cdr3_length_clusters = self.cdr3_length_precluster(waterer)

        hamming_clusters = self.hamming_precluster(cdr3_length_clusters)
        # stripped_clusters = self.run_hmm('forward', waterer.info, self.args.parameter_dir, preclusters=hamming_clusters, stripped=True)
        hmm_clusters = self.run_hmm('forward', waterer.info, self.args.parameter_dir, preclusters=hamming_clusters, hmm_type='k=2', make_clusters=True)

        self.run_hmm('forward', waterer.info, self.args.parameter_dir, preclusters=hmm_clusters, hmm_type='k=preclusters', prefix='k-', make_clusters=False)
        # self.run_hmm('viterbi', waterer.info, self.args.parameter_dir, preclusters=hmm_clusters, hmm_type='k=preclusters', prefix='k-', make_clusters=False)

        # self.clean(waterer)
        if not self.args.no_clean:
            os.rmdir(self.args.workdir)

    # ----------------------------------------------------------------------------------------
    def run_algorithm(self, algorithm):
        if not os.path.exists(self.args.parameter_dir):
            raise Exception('ERROR ' + self.args.parameter_dir + ' d.n.e')
        waterer = Waterer(self.args, self.input_info, self.reco_info, self.germline_seqs, parameter_dir=self.args.parameter_dir, write_parameters=False)
        waterer.run()

        self.run_hmm(algorithm, waterer.info, parameter_in_dir=self.args.parameter_dir, hmm_type='k=nsets', \
                     count_parameters=self.args.plot_parameters, plotdir=self.args.plotdir)

        # self.clean(waterer)
        if not self.args.no_clean:
            os.rmdir(self.args.workdir)

        # if self.args.plot_performance:
        #     plotting.compare_directories(self.args.plotdir + '/hmm-vs-sw', self.args.plotdir + '/hmm/plots', 'hmm', self.args.plotdir + '/sw/plots', 'smith-water', xtitle='inferred - true', stats='rms')

    # ----------------------------------------------------------------------------------------
    def get_hmm_cmd_str(self, algorithm, csv_infname, csv_outfname, parameter_dir, iproc=-1):
        cmd_str = os.getenv('PWD') + '/packages/ham/bcrham'
        if self.args.slurm:
            cmd_str = 'srun ' + cmd_str
        cmd_str += ' --algorithm ' + algorithm
        cmd_str += ' --chunk-cache '
        cmd_str += ' --n_best_events ' + str(self.args.n_best_events)
        cmd_str += ' --debug ' + str(self.args.debug)
        cmd_str += ' --hmmdir ' + parameter_dir + '/hmms'
        cmd_str += ' --datadir ' + self.args.datadir
        cmd_str += ' --infile ' + csv_infname
        cmd_str += ' --outfile ' + csv_outfname

        workdir = self.args.workdir
        if iproc >= 0:
            workdir += '/hmm-' + str(iproc)
            cmd_str = cmd_str.replace(self.args.workdir, workdir)

        return cmd_str

    # ----------------------------------------------------------------------------------------
    def run_hmm(self, algorithm, sw_info, parameter_in_dir, parameter_out_dir='', preclusters=None, hmm_type='', stripped=False, prefix='', \
                count_parameters=False, plotdir=None, make_clusters=False):  # @parameterfetishist

        if prefix == '' and stripped:
            prefix = 'stripped'
        print '\n%shmm' % prefix
        csv_infname = self.args.workdir + '/' + prefix + '_hmm_input.csv'
        csv_outfname = self.args.workdir + '/' + prefix + '_hmm_output.csv'
        self.write_hmm_input(csv_infname, sw_info, preclusters=preclusters, hmm_type=hmm_type, stripped=stripped, parameter_dir=parameter_in_dir)
        print '    running'
        sys.stdout.flush()
        start = time.time()
        if self.args.n_procs > 1:
            self.split_input(self.args.n_procs, infname=csv_infname, prefix='hmm')
            procs = []
            for iproc in range(self.args.n_procs):
                cmd_str = self.get_hmm_cmd_str(algorithm, csv_infname, csv_outfname, parameter_dir=parameter_in_dir, iproc=iproc)
                procs.append(Popen(cmd_str.split()))
                time.sleep(0.1)
            for proc in procs:
                proc.wait()
            for iproc in range(self.args.n_procs):
                if not self.args.no_clean:
                    os.remove(csv_infname.replace(self.args.workdir, self.args.workdir + '/hmm-' + str(iproc)))
            self.merge_hmm_outputs(csv_outfname)
        else:
            cmd_str = self.get_hmm_cmd_str(algorithm, csv_infname, csv_outfname, parameter_dir=parameter_in_dir)
            check_call(cmd_str.split())

        sys.stdout.flush()
        print '      hmm run time: %.3f' % (time.time()-start)

        hmminfo = self.read_hmm_output(algorithm, csv_outfname, make_clusters=make_clusters, count_parameters=count_parameters, parameter_out_dir=parameter_out_dir, plotdir=plotdir)

        if self.args.pants_seated_clustering:
            viterbicluster.cluster(hmminfo)

        clusters = None
        if make_clusters:
            if self.outfile is not None:
                self.outfile.write('hmm clusters\n')
            else:
                print '%shmm clusters' % prefix
            clusters = Clusterer(self.args.pair_hmm_cluster_cutoff, greater_than=True, singletons=preclusters.singletons)
            clusters.cluster(input_scores=hmminfo, debug=self.args.debug, reco_info=self.reco_info, outfile=self.outfile, plotdir=self.args.plotdir+'/pairscores')

        if self.args.outfname is not None:
            outpath = self.args.outfname
            if self.args.outfname[0] != '/':  # if full output path wasn't specified on the command line
                outpath = os.getcwd() + '/' + outpath
            shutil.copyfile(csv_outfname, outpath)

        if not self.args.no_clean:
            if os.path.exists(csv_infname):  # if only one proc, this will already be deleted
                os.remove(csv_infname)
            os.remove(csv_outfname)

        return clusters

    # ----------------------------------------------------------------------------------------
    def split_input(self, n_procs, infname=None, info=None, prefix='sub'):
        """ 
        If <infname> is specified split the csv info from it into <n_procs> input files in subdirectories labelled with '<prefix>-' within <self.args.workdir>
        If <info> is specified, instead split the list <info> into pieces and return a list of the resulting lists
        """
        if info is None:
            assert infname is not None
            info = []
            with opener('r')(infname) as infile:
                reader = csv.DictReader(infile)
                for line in reader:
                    info.append(line)
        else:
            assert infname is None  # make sure only *one* of 'em is specified
            outlists = []
        queries_per_proc = float(len(info)) / n_procs
        n_queries_per_proc = int(math.ceil(queries_per_proc))
        for iproc in range(n_procs):
            if infname is None:
                outlists.append([])
            else:
                subworkdir = self.args.workdir + '/' + prefix + '-' + str(iproc)
                utils.prep_dir(subworkdir)
                sub_outfile = opener('w')(subworkdir + '/' + os.path.basename(infname))
                writer = csv.DictWriter(sub_outfile, reader.fieldnames)
                writer.writeheader()
            for iquery in range(iproc*n_queries_per_proc, (iproc + 1)*n_queries_per_proc):
                if iquery >= len(info):
                    break
                if infname is None:
                    outlists[-1].append(info[iquery])
                else:
                    writer.writerow(info[iquery])

        if infname is None:
            return outlists

    # ----------------------------------------------------------------------------------------
    def merge_hmm_outputs(self, outfname):
        header = None
        outfo = []
        for iproc in range(self.args.n_procs):
            workdir = self.args.workdir + '/hmm-' + str(iproc)
            with opener('r')(workdir + '/' + os.path.basename(outfname)) as sub_outfile:
                reader = csv.DictReader(sub_outfile)
                header = reader.fieldnames
                for line in reader:
                    outfo.append(line)
            if not self.args.no_clean:
                os.remove(workdir + '/' + os.path.basename(outfname))
                os.rmdir(workdir)

        with opener('w')(outfname) as outfile:
            writer = csv.DictWriter(outfile, header)
            writer.writeheader()
            for line in outfo:
                writer.writerow(line)

    # ----------------------------------------------------------------------------------------
    def cdr3_length_precluster(self, waterer, preclusters=None):
        cdr3lengthfname = self.args.workdir + '/cdr3lengths.csv'
        with opener('w')(cdr3lengthfname) as outfile:
            writer = csv.DictWriter(outfile, ('unique_id', 'second_unique_id', 'cdr3_length', 'second_cdr3_length', 'score'))
            writer.writeheader()
            for query_name, second_query_name in self.get_pairs(preclusters):
                cdr3_length = waterer.info[query_name]['cdr3_length']
                second_cdr3_length = waterer.info[second_query_name]['cdr3_length']
                same_length = cdr3_length == second_cdr3_length
                if not self.args.is_data:
                    assert cdr3_length == int(self.reco_info[query_name]['cdr3_length'])
                    if second_cdr3_length != int(self.reco_info[second_query_name]['cdr3_length']):
                        print 'WARNING did not infer correct cdr3 length'
                        assert False
                writer.writerow({'unique_id':query_name, 'second_unique_id':second_query_name, 'cdr3_length':cdr3_length, 'second_cdr3_length':second_cdr3_length, 'score':int(same_length)})

        clust = Clusterer(0.5, greater_than=True)  # i.e. cluster together if same_length == True
        clust.cluster(cdr3lengthfname, debug=False)
        os.remove(cdr3lengthfname)
        return clust

    # ----------------------------------------------------------------------------------------
    def get_pairs(self, preclusters=None):
        """ Get all unique the pairs of sequences in input_info, skipping where preclustered out """
        all_pairs = itertools.combinations(self.input_info.keys(), 2)
        if preclusters == None:
            print '    ?? lines (no preclustering)'  # % len(list(all_pairs)) NOTE I'm all paranoid the list conversion will be slow (although it doesn't seem to be a.t.m.)
            return all_pairs
        else:  # if we've already run preclustering, skip the pairs that we know aren't matches
            preclustered_pairs = []
            n_lines, n_preclustered, n_previously_preclustered, n_removable, n_singletons = 0, 0, 0, 0, 0
            for a_name, b_name in all_pairs:
                key = utils.get_key((a_name, b_name))
                # NOTE shouldn't need this any more:
                if a_name not in preclusters.query_clusters or b_name not in preclusters.query_clusters:  # singletons (i.e. they were already preclustered into their own group)
                    n_singletons += 1
                    continue
                if key not in preclusters.pairscores:  # preclustered out in a previous preclustering step
                    n_previously_preclustered += 1
                    continue
                if preclusters.query_clusters[a_name] != preclusters.query_clusters[b_name]:  # not in same cluster
                    n_preclustered += 1
                    continue
                if preclusters.is_removable(preclusters.pairscores[key]):  # in same cluster, but score (link) is long. i.e. *this* pair is far apart, but other seqs to which they are linked are close to each other
                    n_removable += 1
                    continue
                preclustered_pairs.append((a_name, b_name))
                n_lines += 1
            print '    %d lines (%d preclustered out, %d removable links, %d singletons, %d previously preclustered)' % (n_lines, n_preclustered, n_removable, n_singletons, n_previously_preclustered)
            return preclustered_pairs

    # ----------------------------------------------------------------------------------------
    def get_hamming_distances(self, pairs):  #, return_info):
        # NOTE duplicates a function in utils
        return_info = []
        for query_a, query_b in pairs:
            seq_a = self.input_info[query_a]['seq']
            seq_b = self.input_info[query_b]['seq']
            if self.args.truncate_pairs:  # chop off the left side of the longer one if they're not the same length
                min_length = min(len(seq_a), len(seq_b))
                seq_a = seq_a[-min_length : ]
                seq_b = seq_b[-min_length : ]
                chopped_off_left_sides = True
            mutation_frac = utils.hamming(seq_a, seq_b) / float(len(seq_a))
            return_info.append({'id_a':query_a, 'id_b':query_b, 'score':mutation_frac})

        return return_info

    # ----------------------------------------------------------------------------------------
    def hamming_precluster(self, preclusters=None):
        assert self.args.truncate_pairs
        start = time.time()
        print 'hamming clustering'
        chopped_off_left_sides = False
        hamming_info = []
        all_pairs = self.get_pairs(preclusters)
        # print '    getting pairs: %.3f' % (time.time()-start); start = time.time()
        # all_pairs = itertools.combinations(self.input_info.keys(), 2)
        if self.args.n_fewer_procs > 1:
            pool = Pool(processes=self.args.n_fewer_procs)
            subqueries = self.split_input(self.args.n_fewer_procs, info=list(all_pairs), prefix='hamming')  # NOTE 'casting' to a list here makes me nervous!
            sublists = []
            for queries in subqueries:
                sublists.append([])
                for id_a, id_b in queries:
                    sublists[-1].append({'id_a':id_a, 'id_b':id_b, 'seq_a':self.input_info[id_a]['seq'], 'seq_b':self.input_info[id_b]['seq']})
            
            # print '    preparing info: %.3f' % (time.time()-start); start = time.time()
            subinfos = pool.map(utils.get_hamming_distances, sublists)
            # NOTE this starts the proper number of processes, but they seem to end up i/o blocking or something (wait % stays at zero, but they each only get 20 or 30 %cpu on stoat)
            pool.close()
            pool.join()
            # print '    starting pools: %.3f' % (time.time()-start); start = time.time()
    
            for isub in range(len(subinfos)):
                hamming_info += subinfos[isub]
            # print '    merging pools: %.3f' % (time.time()-start); start = time.time()
        else:
            hamming_info = self.get_hamming_distances(all_pairs)

        if self.outfile is not None:
            self.outfile.write('hamming clusters\n')

        clust = Clusterer(self.args.hamming_cluster_cutoff, greater_than=False)  # NOTE this 0.5 is reasonable but totally arbitrary
        clust.cluster(input_scores=hamming_info, debug=self.args.debug, outfile=self.outfile, reco_info=self.reco_info)
        # print '    clustering: %.3f' % (time.time()-start); start = time.time()

        if chopped_off_left_sides:
            print 'WARNING encountered unequal-length sequences, so chopped off the left-hand sides of each'
        print '    hamming time: %.3f' % (time.time()-start)

        return clust
    
    # ----------------------------------------------------------------------------------------
    def write_hmms(self, parameter_dir, sw_matches):
        print 'writing hmms with info from %s' % parameter_dir
        start = time.time()
        from hmmwriter import HmmWriter
        hmm_dir = parameter_dir + '/hmms'
        utils.prep_dir(hmm_dir, '*.yaml')

        gene_list = self.args.only_genes
        if gene_list == None:  # if specific genes weren't specified, do the ones for which we have matches
            gene_list = []
            for region in utils.regions:
                for gene in self.germline_seqs[region]:
                    if sw_matches == None or gene in sw_matches:  # shouldn't be None really, but I'm testing something
                        gene_list.append(gene)

        for gene in gene_list:
            if self.args.debug:
                print '  %s' % utils.color_gene(gene)
            writer = HmmWriter(parameter_dir, hmm_dir, gene, self.args.naivety,
                               self.germline_seqs[utils.get_region(gene)][gene],
                               self.args)
            writer.write()

        print '    time to write hmms: %.3f' % (time.time()-start)

    # ----------------------------------------------------------------------------------------
    def check_hmm_existence(self, gene_list, skipped_gene_matches, parameter_dir, query_name, second_query_name=None):
        """ Check if hmm model file exists, and if not remove gene from <gene_list> and print a warning """
        # first get the list of genes for which we don't have hmm files
        if len(glob.glob(parameter_dir + '/hmms/*.yaml')) == 0:
            print 'ERROR no yamels in %s' % parameter_dir
            sys.exit()

        genes_to_remove = []
        for gene in gene_list:
            hmmfname = parameter_dir + '/hmms/' + utils.sanitize_name(gene) + '.yaml'
            if not os.path.exists(hmmfname):
                # if self.args.debug:
                #     print '    WARNING %s removed from match list for %s %s (not in %s)' % (utils.color_gene(gene), query_name, '' if second_query_name==None else second_query_name, os.path.dirname(hmmfname))
                skipped_gene_matches.add(gene)
                genes_to_remove.append(gene)

        # then remove 'em from <gene_list>
        for gene in genes_to_remove:
            gene_list.remove(gene)

    # ----------------------------------------------------------------------------------------
    def all_regions_present(self, gene_list, skipped_gene_matches, query_name, second_query_name=None):
        # and finally, make sure we're left with at least one gene in each region
        for region in utils.regions:
            if 'IGH' + region.upper() not in ':'.join(gene_list):
                print '       no %s genes in %s for %s %s' % (region, ':'.join(gene_list), query_name, '' if second_query_name==None else second_query_name)
                print '          skipped %s' % (':'.join(skipped_gene_matches))
                print 'ERROR giving up on query'
                return False

        return True

    # ----------------------------------------------------------------------------------------
    def combine_queries(self, sw_info, query_names, parameter_dir, stripped=False, skipped_gene_matches=None):
        combo = {
            'k_v':{'min':99999, 'max':-1},
            'k_d':{'min':99999, 'max':-1},
            'only_genes':[],
            'seqs':[]
        }
        min_length = -1
        for name in query_names:  # first find the min length, so we know how much we'll have to chop off of each sequence
            if min_length == -1 or len(sw_info[name]['seq']) < min_length:
                min_length = len(sw_info[name]['seq'])
        for name in query_names:
            info = sw_info[name]
            query_seq = self.input_info[name]['seq']
            chop = 0
            if self.args.truncate_pairs:  # chop off the left side of the sequence if it's longer than min_length
                chop = max(0, len(query_seq) - min_length)
                query_seq = query_seq[ : min_length]
            combo['seqs'].append(query_seq)
    
            combo['k_v']['min'] = min(info['k_v']['min'] - chop, combo['k_v']['min'])
            combo['k_v']['max'] = max(info['k_v']['max'] - chop, combo['k_v']['max'])
            combo['k_d']['min'] = min(info['k_d']['min'], combo['k_d']['min'])
            combo['k_d']['max'] = max(info['k_d']['max'], combo['k_d']['max'])
    
            only_genes = info['all'].split(':')
            self.check_hmm_existence(only_genes, skipped_gene_matches, parameter_dir, name)
            if stripped:  # strip down the hmm -- only use the single best gene for each sequence, and don't fuzz at all
                assert False  # need to check some things here
                # only_genes = [ a_info[region + '_gene'] for region in utils.regions ]
                # b_only_genes = [ b_info[region + '_gene'] for region in utils.regions ]
                # k_v['min'] = a_info['k_v']['best'] - a_chop  # NOTE since this only uses <a_info>, you shouldn't expect it to be very accurate
                # k_v['max'] = k_v['min'] + 1
                # k_d['min'] = a_info['k_d']['best']
                # k_d['max'] = k_d['min'] + 1
    
            combo['only_genes'] = list(set(only_genes) | set(combo['only_genes']))  # NOTE using both sets of genes (from both query seqs) like this *really* helps,

        # self.check_hmm_existence(combo['only_genes'], skipped_gene_matches, parameter_dir, name)  # this should be superfluous now
        if not self.all_regions_present(combo['only_genes'], skipped_gene_matches, query_names):
            return {}

        return combo

    # ----------------------------------------------------------------------------------------
    def remove_sw_failures(self, query_names, sw_info):
        # if any of the queries in <query_names> was unproductive, skip the whole kitnkaboodle
        unproductive = False
        for qrn in query_names:
            if qrn in sw_info['skipped_unproductive_queries']:
                # print '    skipping unproductive %s along with %s' % (query_names[0], ' '.join(query_names[1:]))
                unproductive = True
        if unproductive:
            return []

        # otherwise they should be in sw_info, but doesn't hurt to check
        return_names = []
        for name in query_names:
            if name in sw_info:
                return_names.append(name)
            else:
                print '    %s not found in sw info' % ' '.join([str(qn) for qn in query_names])
        return return_names

    # ----------------------------------------------------------------------------------------
    def write_hmm_input(self, csv_fname, sw_info, parameter_dir, preclusters=None, hmm_type='', pair_hmm=False, stripped=False):
        print '    writing input'
        csvfile = opener('w')(csv_fname)
        start = time.time()

        # write header
        header = ['names', 'k_v_min', 'k_v_max', 'k_d_min', 'k_d_max', 'only_genes', 'seqs']  # I wish I had a good c++ csv reader 
        csvfile.write(' '.join(header) + '\n')

        skipped_gene_matches = set()
        assert hmm_type != ''
        if hmm_type == 'k=1':  # single vanilla hmm
            nsets = [[qn] for qn in self.input_info.keys()]
        elif hmm_type == 'k=2':  # pair hmm
            nsets = self.get_pairs(preclusters)
        elif hmm_type == 'k=preclusters':  # run the k-hmm on each cluster in <preclusters>
            assert preclusters != None
            nsets = [ val for key, val in preclusters.id_clusters.items() if len(val) > 1 ]  # <nsets> is a list of sets (well, lists) of query names
            # nsets = []
            # for cluster in preclusters.id_clusters.values():
            #     nsets += itertools.combinations(cluster, 5)
        elif hmm_type == 'k=nsets':  # run on *every* combination of queries which has length <self.args.n_sets>
            if self.args.all_combinations:
                nsets = itertools.combinations(self.input_info.keys(), self.args.n_sets)
            else:  # put the first n together, and the second group of n (not the self.input_info is and OrderedDict)
                nsets = []
                keylist = self.input_info.keys()
                this_set = []
                for iquery in range(len(keylist)):
                    if iquery % self.args.n_sets == 0:  # every nth query, start a new group
                        if len(this_set) > 0:
                            nsets.append(this_set)
                        this_set = []
                    this_set.append(keylist[iquery])
                if len(this_set) > 0:
                    nsets.append(this_set)
        else:
            assert False

        for query_names in nsets:
            non_failed_names = self.remove_sw_failures(query_names, sw_info)
            if len(non_failed_names) == 0:
                continue
            combined_query = self.combine_queries(sw_info, non_failed_names, parameter_dir, stripped=stripped, skipped_gene_matches=skipped_gene_matches)
            if len(combined_query) == 0:  # didn't find all regions
                continue
            csvfile.write('%s %d %d %d %d %s %s\n' %  # NOTE csv.DictWriter can handle tsvs, so this should really be switched to use that
                          (':'.join([str(qn) for qn in non_failed_names]),
                           combined_query['k_v']['min'], combined_query['k_v']['max'],
                           combined_query['k_d']['min'], combined_query['k_d']['max'],
                           ':'.join(combined_query['only_genes']),
                           ':'.join(combined_query['seqs'])))

        if len(skipped_gene_matches) > 0:
            print '    not found in %s, i.e. were never the best sw match for any query, so removing from consideration for hmm:' % (parameter_dir)
            for region in utils.regions:
                print '      %s: %s' % (region, ' '.join([utils.color_gene(gene) for gene in skipped_gene_matches if utils.get_region(gene) == region]))

        csvfile.close()
        print '        input write time: %.3f' % (time.time()-start)

    # ----------------------------------------------------------------------------------------
    def read_hmm_output(self, algorithm, hmm_csv_outfname, make_clusters=True, count_parameters=False, parameter_out_dir=None, plotdir=None):
        print '    read output'
        if count_parameters:
            assert parameter_out_dir is not None
            assert plotdir is not None
        pcounter = ParameterCounter(self.germline_seqs) if count_parameters else None
        true_pcounter = ParameterCounter(self.germline_seqs) if (count_parameters and not self.args.is_data) else None
        perfplotter = PerformancePlotter(self.germline_seqs, plotdir + '/hmm/performance', 'hmm') if self.args.plot_performance else None

        n_processed = 0
        hmminfo = []
        with opener('r')(hmm_csv_outfname) as hmm_csv_outfile:
            reader = csv.DictReader(hmm_csv_outfile)
            last_key = None
            boundary_error_queries = []
            for line in reader:
                utils.intify(line, splitargs=('unique_ids', 'seqs'))
                ids = line['unique_ids']
                this_key = utils.get_key(ids)
                same_event = from_same_event(self.args.is_data, True, self.reco_info, ids)
                id_str = ''.join(['%20s ' % i for i in ids])

                # check for errors
                if last_key != this_key:  # if this is the first line for this set of ids (i.e. the best viterbi path or only forward score)
                    if line['errors'] != None and 'boundary' in line['errors'].split(':'):
                        boundary_error_queries.append(':'.join([str(uid) for uid in ids]))
                    else:
                        assert len(line['errors']) == 0

                if algorithm == 'viterbi':
                    line['seq'] = line['seqs'][0]  # add info for the best match as 'seq'
                    line['unique_id'] = ids[0]
                    utils.add_match_info(self.germline_seqs, line, self.cyst_positions, self.tryp_positions, debug=(self.args.debug > 0))

                    if last_key != this_key or self.args.plot_all_best_events:  # if this is the first line (i.e. the best viterbi path) for this query (or query pair), print the true event
                        n_processed += 1
                        if self.args.debug:
                            print '%s   %d' % (id_str, same_event)
                        if line['cdr3_length'] != -1 or not self.args.skip_unproductive:  # if it's productive, or if we're not skipping unproductive rearrangements
                            hmminfo.append(dict([('unique_id', line['unique_ids'][0]), ] + line.items()))
                            if pcounter is not None:  # increment counters (but only for the best [first] match)
                                pcounter.increment(line)
                            if true_pcounter is not None:  # increment true counters
                                true_pcounter.increment(self.reco_info[ids[0]])
                            if perfplotter is not None:
                                perfplotter.evaluate(self.reco_info[ids[0]], line)

                    if self.args.debug:
                        self.print_hmm_output(line, print_true=(last_key != this_key), perfplotter=perfplotter)
                    line['seq'] = None
                    line['unique_id'] = None

                else:  # for forward, write the pair scores to file to be read by the clusterer
                    if not make_clusters:  # self.args.debug or 
                        print '%3d %10.3f    %s' % (same_event, float(line['score']), id_str)
                    if line['score'] == '-nan':
                        print '    WARNING encountered -nan, setting to -999999.0'
                        score = -999999.0
                    else:
                        score = float(line['score'])
                    if len(ids) == 2:
                        hmminfo.append({'id_a':line['unique_ids'][0], 'id_b':line['unique_ids'][1], 'score':score})
                    n_processed += 1

                last_key = utils.get_key(ids)

        if pcounter is not None:
            pcounter.write(parameter_out_dir)
            if not self.args.no_plot:
                pcounter.plot(plotdir, subset_by_gene=True, cyst_positions=self.cyst_positions, tryp_positions=self.tryp_positions)
        if true_pcounter is not None:
            true_pcounter.write(parameter_out_dir + '/true')
            if not self.args.no_plot:
                true_pcounter.plot(plotdir + '/true', subset_by_gene=True, cyst_positions=self.cyst_positions, tryp_positions=self.tryp_positions)
        if perfplotter is not None:
            perfplotter.plot()

        print '  processed %d queries' % n_processed
        if len(boundary_error_queries) > 0:
            print '    %d boundary errors (%s)' % (len(boundary_error_queries), ', '.join(boundary_error_queries))

        return hmminfo

    # ----------------------------------------------------------------------------------------
    def get_true_clusters(self, ids):
        clusters = {}
        for uid in ids:
            rid = self.reco_info[uid]['reco_id']
            found = False
            for clid in clusters:
                if rid == clid:
                    clusters[clid].append(uid)
                    found = True
                    break
            if not found:
                clusters[rid] = [uid]
        return clusters

    # ----------------------------------------------------------------------------------------
    def print_hmm_output(self, line, print_true=False, perfplotter=None):
        out_str_list = []
        ilabel = ''
        if print_true and not self.args.is_data:  # first print true event (if this is simulation)
            for reco_id, uids in self.get_true_clusters(line['unique_ids']).items():
                for iid in range(len(uids)):
                    out_str_list.append(utils.print_reco_event(self.germline_seqs, self.reco_info[uids[iid]], extra_str='    ', return_string=True, label='true:', one_line=(iid!=0)))
            ilabel = 'inferred:'

        out_str_list.append(utils.print_reco_event(self.germline_seqs, line, extra_str='    ', return_string=True, label=ilabel))
        for iextra in range(1, len(line['unique_ids'])):
            line['seq'] = line['seqs'][iextra]
            out_str_list.append(utils.print_reco_event(self.germline_seqs, line, extra_str='    ', return_string=True, one_line=True))

        # if not self.args.is_data:
        #     self.print_performance_info(line, perfplotter=perfplotter)

        print ''.join(out_str_list),

    # ----------------------------------------------------------------------------------------
    def print_performance_info(self, line, perfplotter=None):
        true_line = self.reco_info[line['unique_id']]
        genes_ok = [ 'ok'  if line[region+'_gene']==true_line[region+'_gene'] else 'no' for region in utils.regions]
        print '         v  d  j   hamming      erosions      insertions'
        print '        %3s%3s%3s' % tuple(genes_ok),
        print '  %3d' % (perfplotter.hamming_distance_to_true_naive(true_line, line, line['unique_id']) if perfplotter != None else -1),
        print '   %4d%4d%4d%4d' % tuple([ int(line[ero+'_del']) - int(true_line[ero+'_del']) for ero in utils.real_erosions]),
        print '   %4d%4d' % tuple([len(line[bound+'_insertion']) - len(true_line[bound+'_insertion']) for bound in utils.boundaries])
