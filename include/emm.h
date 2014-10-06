#ifndef HAM_EMM_H
#define HAM_EMM_H

#include <iostream>
#include <iomanip>
#include <string>
#include <vector>
#include <sstream>

#include "yaml-cpp/yaml.h"
#include "lexicaltable.h"

using namespace std;

namespace ham {

class emm {
  friend class State;
  friend class model;
public:
  emm();
  void parse(YAML::Node config, string is_pair, tracks model_tracks);
  ~emm();
              
  bool pair() { return pair_; }
  size_t get_n_tracks() { return tracks_->size(); }
  inline double score(Sequence& seq, size_t pos) { return scores.getValue(seq, pos); }
  inline double score(Sequences& seqs, size_t pos) { return scores.getValue(seqs, pos); }
  
  void print();
private:
  double total_;
  bool pair_;
  LexicalTable scores;
  vector<track*>* tracks_;             //Tracks used
  vector<size_t>* track_indices; //Indices of tracks used
};

}
#endif
