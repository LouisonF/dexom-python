
import argparse
import numpy as np
import pandas as pd
from pathlib import Path
from cobra.io import load_json_model, read_sbml_model, load_matlab_model
from dexom_python.imat import imat, create_partial_variables, create_full_variables
from dexom_python.model_functions import load_reaction_weights
from dexom_python.result_functions import read_solution, get_binary_sol, write_solution


class RxnEnumSolution(object):
	def __init__(self,
				 all_solutions, unique_solutions, all_binary, unique_binary, all_reactions=None, unique_reactions=None):
		self.all_solutions = all_solutions
		self.unique_solutions = unique_solutions
		self.all_binary = all_binary
		self.unique_binary = unique_binary
		self.all_reactions = all_reactions
		self.unique_reactions = unique_reactions


def rxn_enum(model, reaction_weights, rxn_list, prev_sol, eps=1., thr=1e-1, tlim=None, feas=1e-6, mipgap=1e-3,
			 obj_tol=1e-2):
	"""
	Reaction enumeration method

	Parameters
	----------
	model: cobrapy Model
	reaction_weights: dict
		keys = reactions and values = weights
	eps: float
		activation threshold in imat
	thr: float
		detection threshold of activated reactions
	tlim: int
		time limit for imat
	tol: float
		tolerance for imat
	obj_tol: float
		variance allowed in the objective_values of the solutions
	out_name: str
		name of output files without format
	Returns
	-------
	solution: RxnEnumSolution object
	"""
	prev_sol_bin = get_binary_sol(prev_sol, thr)
	optimal_objective_value = prev_sol.objective_value - prev_sol.objective_value * obj_tol

	all_solutions = [prev_sol]
	all_solutions_binary = [prev_sol_bin]
	unique_solutions = [prev_sol]
	unique_solutions_binary = [prev_sol_bin]
	all_reactions = []  # for each solution, save which reaction was activated/inactived by the algorithm
	unique_reactions = []
	try:
		model.solver = 'cplex'
		model.solver.configuration.presolve = True
	except:
		print("cplex is not available or not properly installed")
	if not rxn_list:
		rxns = list(model.reactions)
		rxn_list = [r.id for r in rxns]
	for idx, rid in enumerate(rxn_list):
		with model as model_temp:
			if rid in model.reactions:
				rxn = model_temp.reactions.get_by_id(rid)
				# for active fluxes, check inactivation
				if prev_sol_bin[idx] == 1:
					rxn.bounds = (0., 0.)
				# for inactive fluxes, check activation
				else:
					upper_bound_temp = rxn.upper_bound
					# for inactive reversible fluxes, check activation in backwards direction
					if rxn.lower_bound < 0.:
						try:
							rxn.upper_bound = -thr
							temp_sol = imat(model_temp, reaction_weights, epsilon=eps,
											threshold=thr, timelimit=tlim, feasibility=feas, mipgaptol=mipgap)
							temp_sol_bin = get_binary_sol(temp_sol, thr)
							if temp_sol.objective_value >= optimal_objective_value:
								all_solutions.append(temp_sol)
								all_solutions_binary.append(temp_sol_bin)
								if temp_sol_bin not in unique_solutions_binary:
									unique_solutions.append(temp_sol)
									unique_solutions_binary.append(temp_sol_bin)
									unique_reactions.append(rid+"_backwards")
						except:
							print("An error occurred with reaction %s_backwards. "
								  "Check feasibility of the model when this reaction is irreversible." % rid)
						finally:
							rxn.upper_bound = upper_bound_temp
					# for all inactive fluxes, check activation in forwards direction
					rxn.lower_bound = thr
				# for all fluxes: compute solution with new bounds
				try:
					temp_sol = imat(model_temp, reaction_weights, epsilon=eps,
									threshold=thr, timelimit=tlim, feasibility=feas, mipgaptol=mipgap)
					temp_sol_bin = [1 if np.abs(flux) >= thr else 0 for flux in temp_sol.fluxes]
					if temp_sol.objective_value >= optimal_objective_value:
						all_solutions.append(temp_sol)
						all_solutions_binary.append(temp_sol_bin)
						all_reactions.append(rid)
						if temp_sol_bin not in unique_solutions_binary:
							unique_solutions.append(temp_sol)
							unique_solutions_binary.append(temp_sol_bin)
							unique_reactions.append(rid)
				except:
					print("An error occurred with reaction %s. "
						  "Check feasibility of the model when this reaction is blocked/irreversible" % rid)

	solution = RxnEnumSolution(all_solutions, unique_solutions, all_solutions_binary, unique_solutions_binary,
							   all_reactions, unique_reactions)
	return solution


def rxn_enum_single_loop(model, reaction_weights, rec_id, new_rec_state, out_name, eps=1e-2, thr=1e-5, tlim=None,
						 feas=1e-6, mipgap=1e-3):
	with model as model_temp:
		if rec_id not in model.reactions:
			print("reaction not found in model")
			return 0
		rxn = model_temp.reactions.get_by_id(rec_id)
		if int(new_rec_state) == 0:
			rxn.bounds = (0., 0.)
		elif int(new_rec_state) == 1:
			rxn.lower_bound = thr
		elif int(new_rec_state) == 2:
			rxn.upper_bound = -thr
		else:
			print("new_rec_state has an incorrect value: %s" % str(new_rec_state))
			return 0
		try:
			sol = imat(model_temp, reaction_weights, epsilon=eps, threshold=thr, timelimit=tlim,
							feasibility=feas, mipgaptol=mipgap)
		except:
			print("This constraint renders the problem unfeasible")
			return 0
	write_solution(sol, thr, out_name)
	return 1


if __name__ == "__main__":
	description = "Performs the reaction enumeration algorithm on a specified list of reactions"

	parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawTextHelpFormatter)
	parser.add_argument("-m", "--model", help="Metabolic model in sbml, matlab, or json format")
	parser.add_argument("-l", "--reaction_list", default=None, help="csv list of reactions to enumerate - if empty, "
																	"will use all reactions in the model")
	parser.add_argument("--range", default="_",
						help="range of reactions to use from the list, in the format 'int_int'")
	parser.add_argument("-r", "--reaction_weights", default=None,
						help="Reaction weights in csv format (first row: reaction names, second row: weights)")
	parser.add_argument("-p", "--prev_sol", default=None, help="initial imat solution in .txt format")
	parser.add_argument("--epsilon", type=float, default=1e-2,
						help="Activation threshold for highly expressed reactions")
	parser.add_argument("--threshold", type=float, default=1e-5, help="Activation threshold for all reactions")
	parser.add_argument("-t", "--timelimit", type=int, default=None, help="Solver time limit")
	parser.add_argument("--tol", type=float, default=1e-6, help="Solver feasibility tolerance")
	parser.add_argument("--mipgap", type=float, default=1e-3, help="Solver MIP gap tolerance")
	parser.add_argument("--obj_tol", type=float, default=1e-3,
						help="objective value tolerance, as a fraction of the original value")
	parser.add_argument("-o", "--output", default="rxn_enum", help="Path of output files, without format")
	parser.add_argument("--save", action="store_true", default = False, help="Use this flag to save each solution individually")
	args = parser.parse_args()
	#### DEBUG:
	# class args(object):
	# 	def __init__(self,model):
	# 		self.model = model
	#
	# args.model= "input_data/recon2v2_biomass_corrected.json"
	# args.reaction_list = "input_data/recon2_2_reactions.csv"
	# args.range = "5900_6000"
	# args.reaction_weights = "csvs/003016106009.CEL_rh_rl_exprs_25_75_parsed_reactions.csv"
	# args.threshold = 1e-5
	# args.timelimit = 600
	# args.tol = 1e-6
	# args.mipgap = 1e-2
	# args.obj_tol = 1e-3
	# args.output = "working_enum/003016106009.CEL_rxn_enum_59"
	# args.save = False
	# args.prev_sol = False
	# args.epsilon = 1e-2
	#### END
	fileformat = Path(args.model).suffix
	if fileformat == ".sbml" or fileformat == ".xml":
		model = read_sbml_model(args.model)
	elif fileformat == '.json':
		model = load_json_model(args.model)
	elif fileformat == ".mat":
		model = load_matlab_model(args.model)
	else:
		print("Only SBML, JSON, and Matlab formats are supported for the models")
		model = None

	try:
		model.solver = 'cplex'
		model.solver.configuration.presolve = True
	except:
		print("cplex is not available or not properly installed")

	reaction_weights = {}
	if args.reaction_weights:
		reaction_weights = load_reaction_weights(args.reaction_weights)

	rxn_list = []
	if args.reaction_list:
		df = pd.read_csv(args.reaction_list, header=None)
		reactions = [x for x in df.unstack().values]
		rxn_range = args.range.split("_")
		if rxn_range[0] == '':
			start = 0
		else:
			start = int(rxn_range[0])
		if rxn_range[1] == '':
			rxn_list = reactions[start:]
		elif int(rxn_range[1]) > len(reactions):
			rxn_list = reactions[start:]
		else:
			rxn_list = reactions[start:int(rxn_range[1])]

	if args.prev_sol:
		initial_solution, initial_binary = read_solution(args.prev_sol, model, reaction_weights)
		model = create_partial_variables(model, reaction_weights, args.epsilon)
	else:
		initial_solution = imat(model, reaction_weights, epsilon=args.epsilon, threshold=args.threshold,
								timelimit=args.timelimit, feasibility=args.tol, mipgaptol=args.mipgap)

		solution = rxn_enum(model=model, rxn_list=rxn_list, prev_sol=initial_solution, reaction_weights=reaction_weights,
							eps=args.epsilon, thr=args.threshold, tlim=args.timelimit, feas=args.tol, mipgap=args.mipgap,
							obj_tol=args.obj_tol)

		uniques = pd.DataFrame(solution.unique_binary)
		uniques.to_csv(args.output+"_solutions.csv")

		if args.save:
			for i in range(len(solution.unique_solutions)):
				write_solution(solution.unique_solutions[i], args.threshold, args.output+"_solution_"+str(i)+".csv")
